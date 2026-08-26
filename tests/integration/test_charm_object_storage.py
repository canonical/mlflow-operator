# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#

"""Integration tests for Seldon Core Operator/Charm."""

import base64
import logging
import os
import subprocess
import time
from pathlib import Path
from random import choices
from string import ascii_lowercase

import aiohttp
import lightkube
import mlflow
import pytest
import requests
import yaml
from charmed_kubeflow_chisme.kubernetes import KubernetesResourceHandler
from charmed_kubeflow_chisme.testing import (
    CharmSpec,
    assert_alert_rules,
    assert_grafana_dashboards,
    assert_logging,
    assert_metrics_endpoint,
    assert_security_context,
    generate_container_securitycontext_map,
    get_alert_rules,
    get_grafana_dashboards,
    get_pod_names,
)
from charmed_kubeflow_chisme.testing.s3_integration import deploy_and_assert_s3_integrator
from charms_dependencies import (
    ISTIO_GATEWAY,
    ISTIO_PILOT,
    METACONTROLLER_OPERATOR,
    MINIO,
    MYSQL_K8S,
    RESOURCE_DISPATCHER,
    S3_INTEGRATOR,
)
from lightkube import codecs
from lightkube.core.exceptions import ApiError
from lightkube.generic_resource import (
    create_namespaced_resource,
    load_in_cluster_generic_resources,
)
from lightkube.resources.core_v1 import Secret, Service
from minio import Minio
from mlflow.tracking import MlflowClient
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_delay, wait_fixed

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]
CONTAINERS_SECURITY_CONTEXT_MAP = generate_container_securitycontext_map(METADATA)
NAMESPACE_FILE = "./tests/integration/namespace.yaml"
PODDEFAULTS_CRD_TEMPLATE = "./tests/integration/crds/poddefaults.yaml"
PODDEFAULTS_SUFFIXES = ["-access-minio", "-minio"]
TESTING_LABELS = ["user.kubeflow.org/enabled"]  # Might be more than one in the future
SECRET_SUFFIX = "-minio-artifact"
TEST_EXPERIMENT_NAME = "test-experiment"

PodDefault = create_namespaced_resource("kubeflow.org", "v1alpha1", "PodDefault", "poddefaults")


def _safe_load_file_to_text(filename: str) -> str:
    """Returns the contents of filename if it is an existing file, else it returns filename."""
    try:
        text = Path(filename).read_text()
    except FileNotFoundError:
        text = filename
    return text


def delete_all_from_yaml(yaml_text: str, lightkube_client: lightkube.Client = None):
    """Deletes all k8s resources listed in a YAML file via lightkube.

    Args:
        yaml_file (str or Path): Either a string filename or a string of valid YAML.  Will attempt
                                 to open a filename at this path, failing back to interpreting the
                                 string directly as YAML.
        lightkube_client: Instantiated lightkube client or None
    """

    if lightkube_client is None:
        lightkube_client = lightkube.Client()

    for obj in codecs.load_all_yaml(yaml_text):
        lightkube_client.delete(type(obj), obj.metadata.name)


@pytest.fixture(scope="session")
def lightkube_client() -> lightkube.Client:
    client = lightkube.Client(field_manager=CHARM_NAME)
    return client


def deploy_k8s_resources(template_files: str):
    lightkube_client = lightkube.Client(field_manager=CHARM_NAME)
    k8s_resource_handler = KubernetesResourceHandler(
        field_manager=CHARM_NAME, template_files=template_files, context={}
    )
    load_in_cluster_generic_resources(lightkube_client)
    k8s_resource_handler.apply()


async def fetch_url(url):
    """Fetch provided URL and return JSON."""
    result = None
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            result = await response.json()
    return result


@pytest.fixture(scope="session")
def namespace(lightkube_client: lightkube.Client):
    yaml_text = _safe_load_file_to_text(NAMESPACE_FILE)
    yaml_rendered = yaml.safe_load(yaml_text)
    for label in TESTING_LABELS:
        yaml_rendered["metadata"]["labels"][label] = "true"
    obj = codecs.from_dict(yaml_rendered)
    lightkube_client.apply(obj)

    yield obj.metadata.name

    delete_all_from_yaml(yaml_text, lightkube_client)


async def setup_istio(ops_test: OpsTest, istio_gateway: CharmSpec, istio_pilot: CharmSpec):
    """Deploy Istio Ingress Gateway and Istio Pilot."""
    await ops_test.model.deploy(
        entity_url=istio_gateway.charm,
        channel=istio_gateway.channel,
        config=istio_gateway.config,
        trust=istio_gateway.trust,
    )
    await ops_test.model.deploy(
        istio_pilot.charm,
        channel=istio_pilot.channel,
        config=istio_pilot.config,
        trust=istio_pilot.trust,
    )
    await ops_test.model.integrate(istio_pilot.charm, istio_gateway.charm)

    await ops_test.model.wait_for_idle(
        apps=[istio_pilot.charm, istio_gateway.charm],
        status="active",
        timeout=60 * 5,
        raise_on_blocked=False,
        raise_on_error=False,
    )


def get_ingress_url(lightkube_client: lightkube.Client, model_name: str):
    gateway_svc = lightkube_client.get(
        Service, "istio-ingressgateway-workload", namespace=model_name
    )
    ingress_record = gateway_svc.status.loadBalancer.ingress[0]
    if ingress_record.ip:
        public_url = f"http://{ingress_record.ip}.nip.io"
    if ingress_record.hostname:
        public_url = f"http://{ingress_record.hostname}"  # Use hostname (e.g. EKS)
    return public_url


async def fetch_response(url, headers):
    """Fetch provided URL and return pair - status and text (int, string)."""
    result_status = 0
    result_text = ""
    async with aiohttp.ClientSession() as session:
        async with session.get(url=url, headers=headers) as response:
            result_status = response.status
            result_text = await response.text()
    return result_status, str(result_text)


def _assert_resource_cleared(lightkube_client, resource, name: str, namespace: str):
    """Assert a previously existing namespaced resource is cleared by resource-dispatcher.

    Raises a retryable AssertionError if the resource is still present, so callers can wrap this
    in a tenacity retry to give the reconciliation loop time to propagate the change.
    """
    try:
        lightkube_client.get(resource, name, namespace=namespace)
    except ApiError as api_error:
        if api_error.status.code == 404:
            return
        raise
    raise AssertionError(
        f"{resource.__name__} '{name}' still exists in namespace '{namespace}'; "
        "expected it to be cleared in proxy mode"
    )


class TestCharm:
    @staticmethod
    def generate_random_string(length: int = 4):
        """Returns a random string of lower case alphabetic characters and given length."""
        return "".join(choices(ascii_lowercase, k=length))

    @pytest.mark.abort_on_fail
    async def test_add_relational_db_with_relation_expect_active(self, ops_test: OpsTest):
        deploy_k8s_resources([PODDEFAULTS_CRD_TEMPLATE])
        await ops_test.model.deploy(
            MINIO.charm,
            channel=MINIO.channel,
            config=MINIO.config,
            trust=MINIO.trust,
        )
        await ops_test.model.deploy(
            MYSQL_K8S.charm,
            channel=MYSQL_K8S.channel,
            series="jammy",
            config=MYSQL_K8S.config,
            trust=MYSQL_K8S.trust,
        )
        await ops_test.model.wait_for_idle(
            apps=[MINIO.charm, MYSQL_K8S.charm],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )
        await ops_test.model.integrate(f"{MINIO.charm}:object-storage", CHARM_NAME)
        await ops_test.model.integrate(MYSQL_K8S.charm, CHARM_NAME)

        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
            idle_period=60,
        )
        assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "active"

    @pytest.mark.parametrize("container_name", list(CONTAINERS_SECURITY_CONTEXT_MAP.keys()))
    async def test_container_security_context(
        self,
        ops_test: OpsTest,
        lightkube_client: lightkube.Client,
        container_name: str,
    ):
        """Test that the security context is correctly set for charms and their workloads.

        Verify that all pods' and containers' specs define the expected security contexts, with
        particular emphasis on user IDs and group IDs.
        """
        pod_name = get_pod_names(ops_test.model.name, CHARM_NAME)[0]
        assert_security_context(
            lightkube_client,
            pod_name,
            container_name,
            CONTAINERS_SECURITY_CONTEXT_MAP,
            ops_test.model.name,
        )

    async def test_alert_rules(self, ops_test: OpsTest):
        """Test check charm alert rules and rules defined in relation data bag."""
        app = ops_test.model.applications[CHARM_NAME]
        alert_rules = get_alert_rules()
        logger.info("found alert_rules: %s", alert_rules)
        await assert_alert_rules(app, alert_rules)

    async def test_grafana_dashboards(self, ops_test: OpsTest):
        """Test Grafana dashboards are defined in relation data bag."""
        app = ops_test.model.applications[CHARM_NAME]
        dashboards = get_grafana_dashboards()
        logger.info("found dashboards: %s", dashboards)
        await assert_grafana_dashboards(app, dashboards)

    async def test_metrics_enpoint(self, ops_test: OpsTest):
        """Test metrics_endpoints are defined in relation data bag and their accessibility.

        This function gets all the metrics_endpoints from the relation data bag, checks if
        they are available from the grafana-agent-k8s charm and finally compares them with the
        ones provided to the function.
        """
        app = ops_test.model.applications[CHARM_NAME]
        await assert_metrics_endpoint(app, metrics_port=5000, metrics_path="/metrics")
        await assert_metrics_endpoint(app, metrics_port=8000, metrics_path="/metrics")

    async def test_logging(self, ops_test: OpsTest):
        """Test logging is defined in relation data bag."""
        app = ops_test.model.applications[CHARM_NAME]
        await assert_logging(app)

    @retry(stop=stop_after_delay(300), wait=wait_fixed(10))
    @pytest.mark.abort_on_fail
    async def test_can_connect_exporter_and_get_metrics(self, ops_test: OpsTest):
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        exporter_port = config["mlflow_prometheus_exporter_port"]["value"]
        mlflow_subprocess = subprocess.Popen(
            [
                "kubectl",
                "-n",
                f"{ops_test.model_name}",
                "port-forward",
                f"svc/{CHARM_NAME}",
                f"{exporter_port}:{exporter_port}",
            ]
        )
        time.sleep(10)  # Must wait for port-forward

        url = f"http://localhost:{exporter_port}/metrics"
        response = requests.get(url)
        assert response.status_code == 200
        metrics_text = response.text
        assert 'mlflow_metric{metric_name="num_experiments"} 1.0' in metrics_text
        assert 'mlflow_metric{metric_name="num_registered_models"} 0.0' in metrics_text
        assert 'mlflow_metric{metric_name="num_runs"} 0' in metrics_text

        mlflow_subprocess.terminate()

    @pytest.mark.abort_on_fail
    async def test_mlflow_bucket_exists(self, ops_test):
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        default_bucket_name = config["default_artifact_root"]["value"]

        access_key = MINIO.config["access-key"]
        secret_key = MINIO.config["secret-key"]
        port = MINIO.config["port"]

        minio_subproces = subprocess.Popen(
            [
                "kubectl",
                "-n",
                f"{ops_test.model_name}",
                "port-forward",
                f"svc/{MINIO.charm}",
                f"{port}:{port}",
            ]
        )
        time.sleep(10)  # Must wait for port-forward

        minio_client = Minio(
            f"localhost:{port}",
            access_key=access_key,
            secret_key=secret_key,
            region="us-east-1",  # Must be set otherwise it is not working
            secure=False,  # Change to True if using HTTPS
        )
        # Check if the default_bucket_name bucket exists
        found = minio_client.bucket_exists(bucket_name=default_bucket_name)
        assert found, f"The '{default_bucket_name}' bucket does not exist"

        minio_subproces.terminate()

    @pytest.mark.abort_on_fail
    async def test_can_create_experiment_with_mlflow_library(self, ops_test: OpsTest):
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = config["mlflow_port"]["value"]
        mlflow_subprocess = subprocess.Popen(
            [
                "kubectl",
                "-n",
                f"{ops_test.model_name}",
                "port-forward",
                f"svc/{CHARM_NAME}",
                f"{mlflow_port}:{mlflow_port}",
            ]
        )
        time.sleep(10)  # Must wait for port-forward

        url = f"http://localhost:{mlflow_port}"
        client = MlflowClient(tracking_uri=url)
        response = requests.get(url)
        assert response.status_code == 200
        client.create_experiment(TEST_EXPERIMENT_NAME)
        all_experiments = client.search_experiments()
        assert len(list(filter(lambda e: e.name == TEST_EXPERIMENT_NAME, all_experiments))) == 1

        mlflow_subprocess.terminate()

    @pytest.mark.abort_on_fail
    async def test_deploy_resource_dispatcher(self, ops_test: OpsTest):
        await ops_test.model.deploy(
            entity_url=METACONTROLLER_OPERATOR.charm,
            channel=METACONTROLLER_OPERATOR.channel,
            trust=METACONTROLLER_OPERATOR.trust,
        )
        await ops_test.model.wait_for_idle(
            apps=[METACONTROLLER_OPERATOR.charm],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=120,
        )
        await ops_test.model.deploy(
            RESOURCE_DISPATCHER.charm,
            channel=RESOURCE_DISPATCHER.channel,
            trust=RESOURCE_DISPATCHER.trust,
        )
        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=120,
            idle_period=60,
        )

        await ops_test.model.relate(
            f"{CHARM_NAME}:pod-defaults", f"{RESOURCE_DISPATCHER.charm}:pod-defaults"
        )
        await ops_test.model.relate(
            f"{CHARM_NAME}:secrets", f"{RESOURCE_DISPATCHER.charm}:secrets"
        )

        await ops_test.model.wait_for_idle(
            apps=[RESOURCE_DISPATCHER.charm],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=1200,
        )

    async def test_ingress_relation(self, ops_test: OpsTest):
        """Setup Istio and relate it to the MLflow."""
        await setup_istio(ops_test, ISTIO_GATEWAY, ISTIO_PILOT)

        await ops_test.model.add_relation(f"{ISTIO_PILOT.charm}:ingress", f"{CHARM_NAME}:ingress")

        await ops_test.model.wait_for_idle(apps=[CHARM_NAME], status="active", timeout=60 * 5)

    @retry(stop=stop_after_delay(600), wait=wait_fixed(10))
    @pytest.mark.abort_on_fail
    async def test_ingress_url(self, lightkube_client, ops_test: OpsTest):
        ingress_url = get_ingress_url(lightkube_client, ops_test.model_name)
        result_status, result_text = await fetch_response(f"{ingress_url}/mlflow/", {})

        # verify that UI is accessible
        assert result_status == 200
        assert len(result_text) > 0

    @pytest.mark.abort_on_fail
    async def test_new_user_namespace_has_manifests(
        self, ops_test: OpsTest, lightkube_client: lightkube.Client, namespace: str
    ):
        time.sleep(30)  # sync can take up to 10 seconds for reconciliation loop to trigger
        secret_name = f"{CHARM_NAME}{SECRET_SUFFIX}"
        secret = lightkube_client.get(Secret, secret_name, namespace=namespace)
        assert secret.data == {
            "AWS_ACCESS_KEY_ID": base64.b64encode(
                MINIO.config["access-key"].encode("utf-8")
            ).decode("utf-8"),
            "AWS_SECRET_ACCESS_KEY": base64.b64encode(
                MINIO.config["secret-key"].encode("utf-8")
            ).decode("utf-8"),
        }
        poddefaults_names = [f"{CHARM_NAME}{suffix}" for suffix in PODDEFAULTS_SUFFIXES]
        for name in poddefaults_names:
            pod_default = lightkube_client.get(PodDefault, name, namespace=namespace)
            assert pod_default is not None

        # MinIO is served over plain HTTP (no TLS CA), so no CA bundle must be embedded in the
        # Secret nor wired into the access-minio PodDefault.
        assert "ca-bundle.pem" not in secret.data
        access_minio_poddefault = lightkube_client.get(
            PodDefault, f"{CHARM_NAME}-access-minio", namespace=namespace
        )
        spec = access_minio_poddefault.spec
        env_var_names = {env_var["name"] for env_var in spec["env"]}
        assert "AWS_CA_BUNDLE" not in env_var_names
        assert not spec.get("volumes")
        assert not spec.get("volumeMounts")

    @pytest.mark.abort_on_fail
    async def test_remove_object_storage_relation_expect_blocked(self, ops_test: OpsTest):
        """Removing the object-storage relation should block the charm."""
        await ops_test.juju(
            "remove-relation",
            f"{CHARM_NAME}:object-storage",
            f"{MINIO.charm}:object-storage",
        )
        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME], status="blocked", raise_on_error=False, timeout=60 * 5
        )

    @pytest.mark.abort_on_fail
    async def test_migrate_to_s3_integrator_expect_active(self, ops_test: OpsTest):
        """Relating s3-integrator via s3-credentials should restore the charm to active."""
        await deploy_and_assert_s3_integrator(
            ops_test.model, s3_integrator=S3_INTEGRATOR, add_ca_chain=True
        )
        await ops_test.model.add_relation(
            f"{CHARM_NAME}:s3-credentials", f"{S3_INTEGRATOR.charm}:s3-credentials"
        )
        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME], status="active", raise_on_error=False, timeout=60 * 10
        )

    @pytest.mark.abort_on_fail
    async def test_new_user_namespace_has_manifests_after_migration(
        self, ops_test: OpsTest, lightkube_client: lightkube.Client, namespace: str
    ):
        """After migrating to s3-integrator, the workload still dispatches user manifests.

        The s3-integrator generates random credentials, so assert that the expected keys are
        dispatched into the user namespace rather than their exact values.
        """
        time.sleep(30)  # sync can take up to 10 seconds for reconciliation loop to trigger
        secret_name = f"{CHARM_NAME}{SECRET_SUFFIX}"
        secret = lightkube_client.get(Secret, secret_name, namespace=namespace)
        # The migrated s3-integrator advertises a TLS CA chain, so the CA bundle is embedded
        # alongside the (randomly generated) credentials for direct client I/O.
        assert set(secret.data.keys()) == {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "ca-bundle.pem",
        }
        for value in secret.data.values():
            assert value
        ca_bundle = base64.b64decode(secret.data["ca-bundle.pem"]).decode("utf-8")
        assert "BEGIN CERTIFICATE" in ca_bundle
        poddefaults_names = [f"{CHARM_NAME}{suffix}" for suffix in PODDEFAULTS_SUFFIXES]
        for name in poddefaults_names:
            pod_default = lightkube_client.get(PodDefault, name, namespace=namespace)
            assert pod_default is not None

        # The access-minio PodDefault must now wire the CA bundle into client pods.
        access_minio_poddefault = lightkube_client.get(
            PodDefault, f"{CHARM_NAME}-access-minio", namespace=namespace
        )
        spec = access_minio_poddefault.spec
        ca_bundle_env = next((env for env in spec["env"] if env["name"] == "AWS_CA_BUNDLE"), None)
        assert ca_bundle_env is not None
        assert ca_bundle_env["value"] == "/etc/mlflow/certs/ca-bundle.pem"
        volume = next((vol for vol in spec["volumes"] if vol["name"] == "s3-ca-bundle"), None)
        assert volume is not None
        assert volume["secret"]["secretName"] == secret_name
        volume_mount = next(
            (vm for vm in spec["volumeMounts"] if vm["name"] == "s3-ca-bundle"), None
        )
        assert volume_mount is not None
        assert volume_mount["mountPath"] == "/etc/mlflow/certs"

    @pytest.mark.abort_on_fail
    async def test_enable_proxy_mode_expect_active(self, ops_test: OpsTest):
        """Enabling serve_artifacts (proxy mode) must keep the charm active."""
        await ops_test.model.applications[CHARM_NAME].set_config({"serve_artifacts": "true"})
        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=60 * 10,
            idle_period=60,
        )
        assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "active"

    @retry(stop=stop_after_delay(600), wait=wait_fixed(10))
    @pytest.mark.abort_on_fail
    async def test_ui_is_accessible_in_proxy_mode(self, lightkube_client, ops_test: OpsTest):
        """The tracking server UI must remain reachable after switching to proxy mode."""
        ingress_url = get_ingress_url(lightkube_client, ops_test.model_name)
        result_status, result_text = await fetch_response(f"{ingress_url}/mlflow/", {})
        assert result_status == 200
        assert len(result_text) > 0

    @retry(stop=stop_after_delay(600), wait=wait_fixed(10), reraise=True)
    @pytest.mark.abort_on_fail
    async def test_proxy_mode_updates_dispatched_manifests(
        self, ops_test: OpsTest, lightkube_client: lightkube.Client, namespace: str
    ):
        """In proxy mode the artifact-store credentials are no longer dispatched to users.

        The minio-artifact Secret and the access-minio PodDefault (which grant direct object
        storage access) must be cleared, while the mlflow PodDefault must remain but expose only
        the tracking URI, since artifacts now flow through the tracking server.
        """
        secret_name = f"{CHARM_NAME}{SECRET_SUFFIX}"
        _assert_resource_cleared(lightkube_client, Secret, secret_name, namespace)

        access_minio_poddefault_name = f"{CHARM_NAME}-access-minio"
        _assert_resource_cleared(
            lightkube_client, PodDefault, access_minio_poddefault_name, namespace
        )

        mlflow_poddefault = lightkube_client.get(
            PodDefault, f"{CHARM_NAME}-minio", namespace=namespace
        )
        env_var_names = {env_var["name"] for env_var in mlflow_poddefault.spec["env"]}
        assert "MLFLOW_TRACKING_URI" in env_var_names
        assert "MLFLOW_S3_ENDPOINT_URL" not in env_var_names

    @pytest.mark.abort_on_fail
    async def test_client_logs_and_fetches_artifact_via_tracking_server(self, ops_test: OpsTest):
        """A client without object-storage access must round-trip artifacts in proxy mode.

        With serve_artifacts enabled, artifacts are proxied through the tracking server, so a
        client that only knows the tracking URI (and has no S3 credentials) must be able to
        complete a full artifact round-trip.
        """
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = config["mlflow_port"]["value"]
        mlflow_subprocess = subprocess.Popen(
            [
                "kubectl",
                "-n",
                f"{ops_test.model_name}",
                "port-forward",
                f"svc/{CHARM_NAME}",
                f"{mlflow_port}:{mlflow_port}",
            ]
        )
        time.sleep(10)  # Must wait for port-forward

        # Scrub any object-storage access from the environment so that a successful artifact
        # round-trip can only be served by the tracking server acting as a proxy.
        object_storage_env_vars = [
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "MLFLOW_S3_ENDPOINT_URL",
            "MLFLOW_TRACKING_URI",
        ]
        saved_env_vars = {key: os.environ.pop(key, None) for key in object_storage_env_vars}
        try:
            tracking_uri = f"http://localhost:{mlflow_port}"
            mlflow.set_tracking_uri(tracking_uri)

            experiment_name = f"{TEST_EXPERIMENT_NAME}-proxy-{self.generate_random_string(6)}"
            experiment_id = mlflow.create_experiment(experiment_name)
            artifact_name = "proxied-artifact.txt"
            artifact_content = f"proxied-{self.generate_random_string(8)}"

            with mlflow.start_run(experiment_id=experiment_id) as run:
                mlflow.log_text(artifact_content, artifact_name)
                run_id = run.info.run_id

            client = MlflowClient(tracking_uri=tracking_uri)
            logged_artifacts = {artifact.path for artifact in client.list_artifacts(run_id)}
            assert artifact_name in logged_artifacts

            downloaded_path = client.download_artifacts(
                artifact_uri=f"runs:/{run_id}/{artifact_name}"
            )
            assert Path(downloaded_path).read_text() == artifact_content
        finally:
            for key, value in saved_env_vars.items():
                if value is not None:
                    os.environ[key] = value
            mlflow_subprocess.terminate()
