# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
#

"""Integration tests for Mlflow."""

import base64
import logging
import os
import subprocess
import time
from pathlib import Path
from random import choices
from string import ascii_lowercase

import lightkube
import mlflow
import pytest
import requests
import yaml
from charmed_kubeflow_chisme.kubernetes import KubernetesResourceHandler
from charmed_kubeflow_chisme.testing import (
    ISTIO_INGRESS_K8S_APP,
    ISTIO_INGRESS_ROUTE_ENDPOINT,
    assert_alert_rules,
    assert_grafana_dashboards,
    assert_logging,
    assert_metrics_endpoint,
    assert_path_reachable_through_ingress,
    assert_security_context,
    deploy_and_integrate_service_mesh_charms,
    generate_container_securitycontext_map,
    get_alert_rules,
    get_grafana_dashboards,
    get_pod_names,
    integrate_with_service_mesh,
)
from charms_dependencies import (
    KUBEFLOW_PROFILES,
    METACONTROLLER_OPERATOR,
    MINIO,
    MYSQL_K8S,
    RESOURCE_DISPATCHER,
)
from lightkube import codecs
from lightkube.core.exceptions import ApiError
from lightkube.generic_resource import (
    create_global_resource,
    create_namespaced_resource,
    load_in_cluster_generic_resources,
)
from lightkube.resources.core_v1 import Namespace, Secret
from minio import Minio
from mlflow.artifacts import download_artifacts
from mlflow.tracking import MlflowClient
from pytest_operator.plugin import OpsTest
from tenacity import retry, retry_if_exception_type, stop_after_delay, wait_fixed

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]
CONTAINERS_SECURITY_CONTEXT_MAP = generate_container_securitycontext_map(METADATA)
HTTP_PATH = "/mlflow/"
PODDEFAULTS_CRD_TEMPLATE = "./tests/integration/crds/poddefaults.yaml"
PODDEFAULTS_SUFFIXES = ["-access-minio", "-minio"]
SECRET_SUFFIX = "-minio-artifact"
TEST_EXPERIMENT_NAME = "test-experiment"
PROFILE_FILE = "./tests/integration/profile.yaml"

# A second istio-ingress-k8s instance used to verify multiple-ingress support.
SECOND_INGRESS_APP = "istio-ingress-k8s-alt"
INGRESS_CHANNEL = "2/stable"
# Name of the HTTPRoute submitted by mlflow (see charm._ingress_config).
INGRESS_ROUTE_NAME = "http-route"
# Gateway listener section for cleartext HTTP on port 80.
HTTP_SECTION_NAME = "http-80"
# Path matched by the mlflow HTTPRoute.
INGRESS_ROUTE_PATH = HTTP_PATH

PodDefault = create_namespaced_resource("kubeflow.org", "v1alpha1", "PodDefault", "poddefaults")
Profile = create_global_resource("kubeflow.org", "v1", "Profile", "profiles")
# Gateway API generic resources, resolved at runtime via lightkube.
HTTPROUTE_RESOURCE = create_namespaced_resource(
    "gateway.networking.k8s.io", "v1", "HTTPRoute", "httproutes"
)
GATEWAY_RESOURCE = create_namespaced_resource(
    "gateway.networking.k8s.io", "v1", "Gateway", "gateways"
)


def _safe_load_file_to_text(filename: str) -> str:
    """Returns the contents of filename if it is an existing file, else it returns filename."""
    try:
        text = Path(filename).read_text()
    except FileNotFoundError:
        text = filename
    return text


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


async def assert_ui_is_accessible(ops_test: OpsTest):
    """Verify that UI is accessible through the ingress gateway."""
    await assert_path_reachable_through_ingress(
        http_path=HTTP_PATH,
        namespace=ops_test.model.name,
        expected_content_type="text/html",
        expected_response_text="MLflow",
    )


@pytest.fixture(scope="module")
async def profile_namespace(ops_test: OpsTest, lightkube_client: lightkube.Client) -> str:
    """Ensure a kubeflow profile namespace exists for tests and clean it up afterwards."""
    if KUBEFLOW_PROFILES.charm not in ops_test.model.applications:
        pytest.fail("kubeflow-profiles must be deployed before creating a profile")

    profile_manifest = yaml.safe_load(_safe_load_file_to_text(PROFILE_FILE))
    profile_name = profile_manifest["metadata"]["name"]
    profile_manifest["kind"] = Profile.__name__

    load_in_cluster_generic_resources(lightkube_client)
    profile = codecs.from_dict(profile_manifest)
    try:
        lightkube_client.apply(profile)
    except ApiError as err:
        pytest.fail(f"Failed to apply Profile resource: {err}")

    # Profile reconciliation is asynchronous; wait until the namespace is created.
    for _ in range(18):
        try:
            namespace = lightkube_client.get(Namespace, profile_name)
            assert namespace.metadata.name == profile_name
            break
        except ApiError:
            time.sleep(5)
    else:
        pytest.fail(f"Timed out waiting for namespace '{profile_name}' to be created")

    yield profile_name

    try:
        lightkube_client.delete(Profile, profile_name)
    except ApiError:
        pass

    try:
        lightkube_client.delete(Namespace, profile_name)
    except ApiError:
        pass


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
    async def test_can_create_experiment_with_mlflow_library_via_port_forward(
        self, ops_test: OpsTest
    ):
        """Create an experiment with the MLflow client through kubectl port-forward."""
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

    async def test_mesh_and_ingress_integrations(self, ops_test: OpsTest):
        """Setup Istio in ambient mode to include MLflow and any subsidiary charms in the mesh."""
        # deploy charms providing the service mesh and the ingress while relating MLflow to them:
        await deploy_and_integrate_service_mesh_charms(CHARM_NAME, ops_test.model)

        # including subsidiary charms to the service mesh:
        await integrate_with_service_mesh(
            MINIO.charm, ops_test.model, relate_to_ingress_route_endpoint=False
        )
        await ops_test.model.wait_for_idle(
            apps=[MINIO.charm],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )

    @retry(stop=stop_after_delay(600), wait=wait_fixed(10))
    @pytest.mark.abort_on_fail
    async def test_deploy_kubeflow_profiles(self, ops_test: OpsTest):
        """Deploy kubeflow-profiles in ambient mode and integrate it with the service mesh."""
        ambient_config = KUBEFLOW_PROFILES.config | {
            "istio-gateway-namespace": ops_test.model_name,
        }

        if KUBEFLOW_PROFILES.charm not in ops_test.model.applications:
            await ops_test.model.deploy(
                KUBEFLOW_PROFILES.charm,
                channel=KUBEFLOW_PROFILES.channel,
                config=ambient_config,
                trust=KUBEFLOW_PROFILES.trust,
            )

        await ops_test.model.wait_for_idle(
            apps=[KUBEFLOW_PROFILES.charm],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=900,
        )

        await integrate_with_service_mesh(
            KUBEFLOW_PROFILES.charm,
            ops_test.model,
            relate_to_ingress_route_endpoint=False,
        )
        await ops_test.model.wait_for_idle(
            apps=[KUBEFLOW_PROFILES.charm],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=900,
        )

    @retry(stop=stop_after_delay(600), wait=wait_fixed(10))
    @pytest.mark.abort_on_fail
    async def test_ui_is_accessible(self, lightkube_client, ops_test: OpsTest):
        """Verify that UI is accessible through the ingress gateway."""
        await assert_ui_is_accessible(ops_test)

    @retry(
        stop=stop_after_delay(300),
        wait=wait_fixed(10),
        retry=retry_if_exception_type(subprocess.CalledProcessError),
        reraise=True,
    )
    @pytest.mark.abort_on_fail
    async def test_can_create_experiment_from_user_namespace(
        self, ops_test: OpsTest, profile_namespace: str
    ):
        """Create an experiment from a pod in a namespace created via kubeflow-profiles."""
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = config["mlflow_port"]["value"]

        pod_name = f"mlflow-experimenter-{self.generate_random_string(6)}"
        experiment_name = f"{TEST_EXPERIMENT_NAME}-{self.generate_random_string(6)}"
        logs_result = None

        try:
            tracking_uri = (
                f"http://{CHARM_NAME}.{ops_test.model_name}.svc.cluster.local:{mlflow_port}"
            )
            logger.info(
                f"Creating experiment from namespace={profile_namespace} "
                f"pod={pod_name} experiment={experiment_name} uri={tracking_uri}"
            )
            curl_script = (
                "set -e; "
                f'payload=\'{{"name":"{experiment_name}"}}\'; '
                "curl --fail-with-body -sS --retry 30 --retry-delay 5 --retry-all-errors "
                f"-X POST '{tracking_uri}/api/2.0/mlflow/experiments/create' "
                "-H 'Content-Type: application/json' -d \"$payload\" >/dev/null; "
                "curl --fail-with-body -sS --retry 30 --retry-delay 5 --retry-all-errors -G "
                f"'{tracking_uri}/api/2.0/mlflow/experiments/get-by-name' "
                f"--data-urlencode 'experiment_name={experiment_name}'"
            )

            subprocess.run(
                [
                    "kubectl",
                    "-n",
                    profile_namespace,
                    "run",
                    pod_name,
                    "--image=curlimages/curl:8.8.0",
                    "--restart=Never",
                    "--command",
                    "--",
                    "sh",
                    "-c",
                    curl_script,
                ],
                check=True,
            )
            logger.info(f"Experimenter pod created: {pod_name} in namespace {profile_namespace}")

            subprocess.run(
                [
                    "kubectl",
                    "-n",
                    profile_namespace,
                    "wait",
                    f"pod/{pod_name}",
                    "--for=jsonpath={.status.phase}=Succeeded",
                    "--timeout=180s",
                ],
                check=True,
            )
            logger.info(f"Experimenter pod succeeded: {pod_name}")
            logs_result = subprocess.run(
                ["kubectl", "-n", profile_namespace, "logs", pod_name],
                check=True,
                capture_output=True,
                text=True,
            )
            assert experiment_name in logs_result.stdout
            logger.info(f"Experiment creation verified for: {experiment_name}")
        finally:
            if logs_result is None:
                logs_result = subprocess.run(
                    ["kubectl", "-n", profile_namespace, "logs", pod_name],
                    check=False,
                    capture_output=True,
                    text=True,
                )
            logger.info(
                f"Experimenter pod logs (return_code={logs_result.returncode}):\n"
                f"{logs_result.stdout}"
            )
            if logs_result.stderr:
                logger.info(f"Experimenter pod logs stderr:\n{logs_result.stderr}")
            subprocess.run(
                [
                    "kubectl",
                    "-n",
                    profile_namespace,
                    "delete",
                    "pod",
                    pod_name,
                    "--ignore-not-found",
                ],
                check=False,
            )

    @pytest.mark.abort_on_fail
    async def test_new_user_namespace_has_manifests(
        self,
        ops_test: OpsTest,
        lightkube_client: lightkube.Client,
        profile_namespace: str,
    ):
        time.sleep(30)  # sync can take up to 10 seconds for reconciliation loop to trigger
        secret_name = f"{CHARM_NAME}{SECRET_SUFFIX}"
        secret = lightkube_client.get(Secret, secret_name, namespace=profile_namespace)
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
            pod_default = lightkube_client.get(PodDefault, name, namespace=profile_namespace)
            assert pod_default is not None

    @pytest.mark.abort_on_fail
    async def test_deploy_and_relate_second_ingress(self, ops_test: OpsTest):
        """Deploy a second istio-ingress-k8s and relate it to mlflow.

        mlflow must accept more than one istio-ingress-route relation without
        erroring, so it should remain active after the second ingress is related.
        """
        await ops_test.model.deploy(
            ISTIO_INGRESS_K8S_APP,
            application_name=SECOND_INGRESS_APP,
            channel=INGRESS_CHANNEL,
            trust=True,
        )
        await ops_test.model.wait_for_idle(
            [SECOND_INGRESS_APP],
            raise_on_blocked=False,
            raise_on_error=False,
            wait_for_active=True,
            timeout=60 * 15,
        )

        await ops_test.model.integrate(
            f"{SECOND_INGRESS_APP}:{ISTIO_INGRESS_ROUTE_ENDPOINT}",
            f"{CHARM_NAME}:{ISTIO_INGRESS_ROUTE_ENDPOINT}",
        )
        await ops_test.model.wait_for_idle(
            [CHARM_NAME, SECOND_INGRESS_APP],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=60 * 10,
            idle_period=30,
        )

        assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "active"

    @retry(stop=stop_after_delay(120), wait=wait_fixed(2), reraise=True)
    @pytest.mark.abort_on_fail
    async def test_httproute_attached_to_second_gateway(
        self, ops_test: OpsTest, lightkube_client: lightkube.Client
    ):
        """Verify the HTTPRoute for the second ingress is created and bound to its Gateway.

        The istio-ingress-k8s charm names each route
        ``{source_app}-{route_name}-httproute-{section}-{ingress_app}`` and binds it to a
        Gateway named after the ingress application via ``parentRefs``. We assert that the
        route created for the second ingress is attached to the *second* Gateway (not the
        first) and routes the mlflow path to the mlflow backend.
        """
        namespace = ops_test.model.name

        expected_route_name = (
            f"{CHARM_NAME}-{INGRESS_ROUTE_NAME}-httproute-{HTTP_SECTION_NAME}-{SECOND_INGRESS_APP}"
        )

        # The second Gateway should exist, named after the second ingress application.
        lightkube_client.get(GATEWAY_RESOURCE, name=SECOND_INGRESS_APP, namespace=namespace)

        httproute = lightkube_client.get(
            HTTPROUTE_RESOURCE, name=expected_route_name, namespace=namespace
        )

        parent_refs = httproute.spec["parentRefs"]
        assert len(parent_refs) == 1
        # The route must be attached to the SECOND gateway, not the first.
        assert parent_refs[0]["name"] == SECOND_INGRESS_APP
        assert parent_refs[0]["sectionName"] == HTTP_SECTION_NAME

        # And it must route the mlflow path to the mlflow backend.
        rule = httproute.spec["rules"][0]
        assert rule["matches"][0]["path"]["value"] == INGRESS_ROUTE_PATH
        assert rule["backendRefs"][0]["name"] == CHARM_NAME

    @retry(stop=stop_after_delay(600), wait=wait_fixed(10))
    @pytest.mark.abort_on_fail
    async def test_ui_is_accessible_after_second_ingress(
        self, lightkube_client, ops_test: OpsTest
    ):
        """Verify the UI is still accessible through the ingress after the second ingress."""
        await assert_ui_is_accessible(ops_test)

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
        await assert_ui_is_accessible(ops_test)

    @retry(stop=stop_after_delay(600), wait=wait_fixed(10), reraise=True)
    @pytest.mark.abort_on_fail
    async def test_proxy_mode_updates_dispatched_manifests(
        self, ops_test: OpsTest, lightkube_client: lightkube.Client, profile_namespace: str
    ):
        """In proxy mode the artifact-store credentials are no longer dispatched to users.

        The minio-artifact Secret and the access-minio PodDefault (which grant direct object
        storage access) must be cleared, while the mlflow PodDefault must remain but expose only
        the tracking URI, since artifacts now flow through the tracking server.
        """
        secret_name = f"{CHARM_NAME}{SECRET_SUFFIX}"
        _assert_resource_cleared(lightkube_client, Secret, secret_name, profile_namespace)

        access_minio_poddefault_name = f"{CHARM_NAME}-access-minio"
        _assert_resource_cleared(
            lightkube_client, PodDefault, access_minio_poddefault_name, profile_namespace
        )

        mlflow_poddefault = lightkube_client.get(
            PodDefault, f"{CHARM_NAME}-minio", namespace=profile_namespace
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

            downloaded_path = download_artifacts(artifact_uri=f"runs:/{run_id}/{artifact_name}")
            assert Path(downloaded_path).read_text() == artifact_content
        finally:
            for key, value in saved_env_vars.items():
                if value is not None:
                    os.environ[key] = value
            mlflow_subprocess.terminate()
