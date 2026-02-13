# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
#

"""Integration tests for Mlflow."""

import base64
import logging
import subprocess
import time
from pathlib import Path
from random import choices
from string import ascii_lowercase

import lightkube
import pytest
import requests
import yaml
from charmed_kubeflow_chisme.kubernetes import KubernetesResourceHandler
from charmed_kubeflow_chisme.testing import (
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
from charms_dependencies import METACONTROLLER_OPERATOR, MINIO, MYSQL_K8S, RESOURCE_DISPATCHER
from lightkube import codecs
from lightkube.generic_resource import (
    create_namespaced_resource,
    load_in_cluster_generic_resources,
)
from lightkube.resources.core_v1 import Secret
from minio import Minio
from mlflow.tracking import MlflowClient
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_delay, wait_fixed

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]
CONTAINERS_SECURITY_CONTEXT_MAP = generate_container_securitycontext_map(METADATA)
HTTP_PATH = "/mlflow/"
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
    async def test_ui_is_accessible(self, lightkube_client, ops_test: OpsTest):
        """Verify that UI is accessible through the ingress gateway."""
        await assert_path_reachable_through_ingress(
            http_path=HTTP_PATH,
            namespace=ops_test.model.name,
            expected_content_type="text/html",
            expected_response_text="MLflow",
        )

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
