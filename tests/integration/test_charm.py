# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#

"""Integration tests for Seldon Core Operator/Charm."""

import base64
import logging
import time
from pathlib import Path
from random import choices
from string import ascii_lowercase

import aiohttp
import lightkube
import pytest
import requests
import yaml
from charmed_kubeflow_chisme.kubernetes import KubernetesResourceHandler
from lightkube import codecs
from lightkube.generic_resource import (
    create_namespaced_resource,
    load_in_cluster_generic_resources,
)
from lightkube.resources.core_v1 import Secret
from mlflow.tracking import MlflowClient
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]
RELATIONAL_DB_CHARM_NAME = "mysql-k8s"
OBJECT_STORAGE_CHARM_NAME = "minio"
PROMETHEUS_CHARM_NAME = "prometheus-k8s"
RESOURCE_DISPATCHER_CHARM_NAME = "resource-dispatcher"
METACONTROLLER_CHARM_NAME = "metacontroller-operator"
NAMESPACE_FILE = "./tests/integration/namespace.yaml"
PODDEFAULTS_CRD_TEMPLATE = "./tests/integration/crds/poddefaults.yaml"
PODDEFAULTS_SUFFIXES = ["-access-minio", "-minio"]
TESTING_LABELS = ["user.kubeflow.org/enabled"]  # Might be more than one in the future
OBJECT_STORAGE_CONFIG = {
    "access-key": "minio",
    "secret-key": "minio123",
    "port": "9000",
}
MYSQL_CONFIG = {
    "mysql-interface-database": "mlflow",
    "mysql-interface-user": "mysql",
}
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


class TestCharm:
    @staticmethod
    def generate_random_string(length: int = 4):
        """Returns a random string of lower case alphabetic characters and given length."""
        return "".join(choices(ascii_lowercase, k=length))

    @pytest.mark.abort_on_fail
    async def test_add_relational_db_with_relation_expect_active(self, ops_test: OpsTest):
        deploy_k8s_resources([PODDEFAULTS_CRD_TEMPLATE])
        await ops_test.model.deploy(OBJECT_STORAGE_CHARM_NAME, config=OBJECT_STORAGE_CONFIG)
        await ops_test.model.deploy(
            RELATIONAL_DB_CHARM_NAME,
            channel="8.0/candidate",
            series="jammy",
            config=MYSQL_CONFIG,
            trust=True,
        )
        await ops_test.model.wait_for_idle(
            apps=[OBJECT_STORAGE_CHARM_NAME, RELATIONAL_DB_CHARM_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )
        await ops_test.model.relate(OBJECT_STORAGE_CHARM_NAME, CHARM_NAME)
        await ops_test.model.relate(f"{RELATIONAL_DB_CHARM_NAME}", f"{CHARM_NAME}")

        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )
        assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "active"

    @pytest.mark.abort_on_fail
    async def test_default_bucket_created(self, ops_test: OpsTest):
        """Tests whether the default bucket is auto-generated by mlflow.
        Note: We do not have a test coverage to assert if that the bucket is not created if
        create_default_artifact_root_if_missing==False.
        """
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        default_bucket_name = config["default_artifact_root"]["value"]

        (
            ret_code,
            stdout,
            stderr,
            kubectl_cmd,
        ) = await self.does_minio_bucket_exist(default_bucket_name, ops_test)
        assert ret_code == 0, (
            f"Unable to find bucket named {default_bucket_name}, got "
            f"stdout=\n'{stdout}\n'stderr=\n{stderr}\nUsed command {kubectl_cmd}"
        )

    @staticmethod
    async def does_minio_bucket_exist(bucket_name, ops_test: OpsTest):
        """Connects to the minio server and checks if a bucket exists, checking if a bucket exists.
        Returns:
            Tuple of the return code, stdout, and stderr
        """
        access_key = OBJECT_STORAGE_CONFIG["access-key"]
        secret_key = OBJECT_STORAGE_CONFIG["secret-key"]
        port = OBJECT_STORAGE_CONFIG["port"]
        obj_storage_name = OBJECT_STORAGE_CHARM_NAME
        model_name = ops_test.model_name

        obj_storage_url = f"http://{obj_storage_name}.{model_name}.svc.cluster.local:{port}"

        # Region is not used and doesn't matter, but must set to run in github actions as explained
        # in: https://florian.ec/blog/github-actions-awscli-errors/
        aws_cmd = (
            f"aws --endpoint-url {obj_storage_url} --region us-east-1 s3api head-bucket"
            f" --bucket={bucket_name}"
        )

        # Add random suffix to pod name to avoid collision
        this_pod_name = f"{CHARM_NAME}-minio-bucket-test-{TestCharm.generate_random_string()}"

        kubectl_cmd = (
            "microk8s",
            "kubectl",
            "run",
            "--rm",
            "-i",
            "--restart=Never",
            f"--namespace={ops_test.model_name}",
            this_pod_name,
            f"--env=AWS_ACCESS_KEY_ID={access_key}",
            f"--env=AWS_SECRET_ACCESS_KEY={secret_key}",
            "--image=amazon/aws-cli",
            "--command",
            "--",
            "sh",
            "-c",
            aws_cmd,
        )

        (
            ret_code,
            stdout,
            stderr,
        ) = await ops_test.run(*kubectl_cmd)
        return ret_code, stdout, stderr, " ".join(kubectl_cmd)

    @pytest.mark.abort_on_fail
    async def test_can_create_experiment_with_mlflow_library(self, ops_test: OpsTest):
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        url = f"http://localhost:{config['mlflow_nodeport']['value']}"
        client = MlflowClient(tracking_uri=url)
        response = requests.get(url)
        assert response.status_code == 200
        client.create_experiment(TEST_EXPERIMENT_NAME)
        all_experiments = client.search_experiments()
        assert len(list(filter(lambda e: e.name == TEST_EXPERIMENT_NAME, all_experiments))) == 1

    @pytest.mark.abort_on_fail
    async def test_deploy_resource_dispatcher(self, ops_test: OpsTest):
        await ops_test.model.deploy(
            entity_url=METACONTROLLER_CHARM_NAME,
            channel="latest/edge",
            trust=True,
        )
        await ops_test.model.wait_for_idle(
            apps=[METACONTROLLER_CHARM_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=120,
        )
        await ops_test.model.deploy(
            RESOURCE_DISPATCHER_CHARM_NAME, channel="latest/edge", trust=True
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
            f"{CHARM_NAME}:pod-defaults", f"{RESOURCE_DISPATCHER_CHARM_NAME}:pod-defaults"
        )
        await ops_test.model.relate(
            f"{CHARM_NAME}:secrets", f"{RESOURCE_DISPATCHER_CHARM_NAME}:secrets"
        )

        await ops_test.model.wait_for_idle(
            apps=[RESOURCE_DISPATCHER_CHARM_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
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
                OBJECT_STORAGE_CONFIG["access-key"].encode("utf-8")
            ).decode("utf-8"),
            "AWS_SECRET_ACCESS_KEY": base64.b64encode(
                OBJECT_STORAGE_CONFIG["secret-key"].encode("utf-8")
            ).decode("utf-8"),
        }
        poddefaults_names = [f"{CHARM_NAME}{suffix}" for suffix in PODDEFAULTS_SUFFIXES]
        for name in poddefaults_names:
            pod_default = lightkube_client.get(PodDefault, name, namespace=namespace)
            assert pod_default is not None

    @pytest.mark.abort_on_fail
    async def test_mlflow_alert_rules(self, ops_test: OpsTest):
        await ops_test.model.deploy(PROMETHEUS_CHARM_NAME, channel="latest/stable", trust=True)
        await ops_test.model.relate(PROMETHEUS_CHARM_NAME, CHARM_NAME)
        await ops_test.model.wait_for_idle(
            apps=[PROMETHEUS_CHARM_NAME], status="active", raise_on_blocked=True, timeout=60 * 10
        )

        status = await ops_test.model.get_status()
        prometheus_units = status["applications"]["prometheus-k8s"]["units"]
        prometheus_url = prometheus_units["prometheus-k8s/0"]["address"]

        # obtain scrape targets from Prometheus
        targets_result = await fetch_url(f"http://{prometheus_url}:9090/api/v1/targets")

        # verify that mlflow-server is in the target list
        assert targets_result is not None
        assert targets_result["status"] == "success"
        discovered_labels = targets_result["data"]["activeTargets"][0]["discoveredLabels"]
        assert discovered_labels["juju_application"] == CHARM_NAME

        # obtain alert rules from Prometheus
        rules_url = f"http://{prometheus_url}:9090/api/v1/rules"
        alert_rules_result = await fetch_url(rules_url)

        # verify alerts are available in Prometheus
        assert alert_rules_result is not None
        assert alert_rules_result["status"] == "success"
        rules = alert_rules_result["data"]["groups"][0]["rules"]

        # load alert rules from the rules file
        rules_file_alert_names = []
        with open("src/prometheus_alert_rules/mlflow-server.rule") as f:
            mlflow_server = yaml.safe_load(f.read())
            alerts_list = mlflow_server["groups"][0]["rules"]
            for alert in alerts_list:
                rules_file_alert_names.append(alert["alert"])

        # verify number of alerts is the same in Prometheus and in the rules file
        assert len(rules) == len(rules_file_alert_names)

        # verify that all Mlflow alert rules are in the list and that alerts obtained
        # from Prometheus match alerts in the rules file
        for rule in rules:
            assert rule["name"] in rules_file_alert_names
