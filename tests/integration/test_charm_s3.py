# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
#

"""Integration tests for MLflow against the s3-credentials interface in ambient mode.

This suite mirrors ``test_charm_ambient.py`` (the same service-mesh, ingress and
kubeflow-profiles helpers/fixtures) but provides object storage through the
``s3-integrator`` charm over the ``s3-credentials`` relation instead of MinIO over
``object-storage``. This is the recommended setup for any new MLflow deployments.
"""

import base64
import json
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
    ISTIO_BEACON_K8S_APP,
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
from charmed_kubeflow_chisme.testing.s3_integration import deploy_and_assert_s3_integrator
from charms_dependencies import (
    DATA_INTEGRATOR,
    KUBEFLOW_PROFILES,
    METACONTROLLER_OPERATOR,
    POSTGRESQL_K8S,
    RESOURCE_DISPATCHER,
    S3_INTEGRATOR,
)
from lightkube import codecs
from lightkube.core.exceptions import ApiError
from lightkube.generic_resource import (
    create_global_resource,
    create_namespaced_resource,
    load_in_cluster_generic_resources,
)
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Namespace, Secret
from mlflow.artifacts import download_artifacts
from mlflow.tracking import MlflowClient
from pytest_operator.plugin import OpsTest
from tenacity import Retrying, retry, retry_if_exception_type, stop_after_delay, wait_fixed

# TODO: remove if authentication via IAM charms is implemented in integration tests:
from auth_helpers import IDENTITY_HEADER_NAME, TEST_IDENTITY  # isort:skip

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

# for testing user grants across different MLflow workspaces:
GRANTS_FOR_ADMIN = "admin"
GRANTS_FOR_READ_ONLY = "read-only"
RESOURCE_TYPE_FOR_SUPER_ADMIN = "super-admin"
RESOURCE_TYPE_FOR_WORKSPACE = "workspace"
TEST_IDENTITY_ALIAS = f"identity-that-aliases-{TEST_IDENTITY}"
UPSTREAM_WORKSPACE_HEADER_NAME = "X-MLFLOW-WORKSPACE"
WORKSPACE_WITH_ADMIN_ACCESS = "my-writable-workspace"
WORKSPACE_WITH_ADMIN_ACCESS_UPDATED = "my-reconfigured-writable-workspace"
WORKSPACE_WITH_READ_ONLY_ACCESS = "my-read-only-workspace"
WORKSPACE_WITH_READ_ONLY_ACCESS_UPDATED = "my-reconfigured-read-only-workspace"

PodDefault = create_namespaced_resource("kubeflow.org", "v1alpha1", "PodDefault", "poddefaults")
Profile = create_global_resource("kubeflow.org", "v1", "Profile", "profiles")
# Gateway API generic resources, resolved at runtime via lightkube.
HTTPROUTE_RESOURCE = create_namespaced_resource(
    "gateway.networking.k8s.io", "v1", "HTTPRoute", "httproutes"
)
GATEWAY_RESOURCE = create_namespaced_resource(
    "gateway.networking.k8s.io", "v1", "Gateway", "gateways"
)


class _PortForward:
    """Context manager wrapping a `kubectl port-forward` to the tracking server's K8s Service."""

    def __init__(self, namespace: str, port: int, charm_name: str = CHARM_NAME):
        self._charm_name = charm_name
        self._namespace = namespace
        self._port = port
        self._process = None

    def __enter__(self) -> str:
        self._process = subprocess.Popen(
            [
                "kubectl",
                "-n",
                self._namespace,
                "port-forward",
                f"svc/{self._charm_name}",
                f"{self._port}:{self._port}",
            ]
        )
        time.sleep(10)  # waiting for the port-forwarding to be established
        return f"http://localhost:{self._port}"

    def __exit__(self, *exc):
        if self._process is not None:
            self._process.terminate()


def _safe_load_file_to_text(filename: str) -> str:
    """Returns the contents of filename if it is an existing file, else it returns filename."""
    try:
        text = Path(filename).read_text()
    except FileNotFoundError:
        text = filename
    return text


def _tracking_server_waypoint_principal(namespace: str) -> str:
    """SPIFFE principal of the platform waypoint proxy, for the tracking server restriction.

    Only the platform waypoint proxy is listed: it is the identity mlflow's ztunnel sees for
    in-mesh traffic. Ingress traffic is already permitted by the L4
    AuthorizationPolicy that istio-ingress-k8s creates for the backend when related, so the ingress
    gateway's principal is not needed here.
    """
    # the waypoint's service account is `<model>-<beacon-app>-waypoint`:
    waypoint_service_account = f"{namespace}-{ISTIO_BEACON_K8S_APP}-waypoint"
    return f"cluster.local/ns/{namespace}/sa/{waypoint_service_account}"


@pytest.fixture(scope="session")
def lightkube_client() -> lightkube.Client:
    client = lightkube.Client(field_manager=CHARM_NAME)
    return client


@pytest.fixture(scope="function")
def out_of_mesh_namespace(lightkube_client: lightkube.Client) -> str:
    """Create a namespace that is not enrolled in the ambient mesh, cleaned up after the test."""
    namespace = f"mlflow-outsider-{''.join(choices(ascii_lowercase, k=6))}"
    lightkube_client.create(Namespace(metadata=ObjectMeta(name=namespace)))
    yield namespace
    try:
        lightkube_client.delete(Namespace, namespace)
    except ApiError:
        pass


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
        # TODO: remove if authentication via IAM charms is implemented in integration tests:
        headers={IDENTITY_HEADER_NAME: TEST_IDENTITY},
        namespace=ops_test.model.name,
        expected_content_type="text/html",
        expected_response_text="MLflow",
    )


@pytest.fixture(scope="module")
async def profile_namespace(ops_test: OpsTest, lightkube_client: lightkube.Client):
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
    async def test_add_s3_and_db_relation_expect_active(self, ops_test: OpsTest):
        deploy_k8s_resources([PODDEFAULTS_CRD_TEMPLATE])
        await deploy_and_assert_s3_integrator(
            ops_test.model, add_ca_chain=True, s3_integrator=S3_INTEGRATOR
        )
        await ops_test.model.deploy(
            POSTGRESQL_K8S.charm,
            channel=POSTGRESQL_K8S.channel,
            series="jammy",
            config=POSTGRESQL_K8S.config,
            trust=POSTGRESQL_K8S.trust,
        )
        await ops_test.model.wait_for_idle(
            apps=[S3_INTEGRATOR.charm, POSTGRESQL_K8S.charm],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )
        await ops_test.model.integrate(
            f"{S3_INTEGRATOR.charm}:s3-credentials", f"{CHARM_NAME}:s3-credentials"
        )
        await ops_test.model.integrate(POSTGRESQL_K8S.charm, CHARM_NAME)

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

    # TODO: remove once multi-tenancy is completed:
    @pytest.mark.skip(reason="WIP: /metrics now behind RBAC and exporter not yet credentialed")
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

    # TODO: remove once multi-tenancy is completed:
    @pytest.mark.skip(reason="WIP: /metrics now behind RBAC and exporter not yet credentialed")
    @retry(stop=stop_after_delay(300), wait=wait_fixed(10))
    @pytest.mark.abort_on_fail
    async def test_can_connect_exporter_and_get_metrics(self, ops_test: OpsTest):
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        exporter_port = config["mlflow_prometheus_exporter_port"]["value"]
        # while port-forwarding the metrics exporter for ease of access:
        with _PortForward(ops_test.model_name, exporter_port) as metrics_exporter_url:
            url = f"{metrics_exporter_url}/metrics"
            response = requests.get(url)
            assert response.status_code == 200
            metrics_text = response.text
            assert 'mlflow_metric{metric_name="num_experiments"} 1.0' in metrics_text
            assert 'mlflow_metric{metric_name="num_registered_models"} 0.0' in metrics_text
            assert 'mlflow_metric{metric_name="num_runs"} 0' in metrics_text

    @pytest.mark.abort_on_fail
    async def test_deploy_data_integrator(self, ops_test: OpsTest):
        """Deploy a data-integrator instance, for user grants in subsequent tests."""
        # TODO: remove this command and restore the command below once
        # https://github.com/canonical/data-integrator/pull/328 lands on main, that is on channel
        # "latest/edge", and mind that an explicit Juju-CLI deploy is temporarily required because
        # python-libjuju's `Model.deploy()` breaks with this temporary channel format:
        await ops_test.juju(
            "deploy",
            DATA_INTEGRATOR.charm,
            "--trust",
            "--channel",
            "latest/edge/mlflow-client",
            "--revision",
            "521",
        )
        # await ops_test.model.deploy(
        #     DATA_INTEGRATOR.charm,
        #     channel=DATA_INTEGRATOR.channel,
        #     config=DATA_INTEGRATOR.config,
        #     trust=DATA_INTEGRATOR.trust,
        # )
        await ops_test.model.wait_for_idle(
            apps=[DATA_INTEGRATOR.charm], status="blocked", timeout=600, idle_period=60
        )

    @pytest.mark.abort_on_fail
    async def test_configure_workspace_grants_of_user(self, ops_test: OpsTest):
        """Configure workspace grants for the test user by relating data-integrator."""
        await ops_test.model.applications[DATA_INTEGRATOR.charm].set_config(
            {
                "entity-name": TEST_IDENTITY,
                "entity-permissions": json.dumps(
                    [
                        {
                            "resource_type": RESOURCE_TYPE_FOR_WORKSPACE,
                            "resource_name": WORKSPACE_WITH_ADMIN_ACCESS,
                            "privileges": [GRANTS_FOR_ADMIN],
                        },
                        {
                            "resource_type": RESOURCE_TYPE_FOR_WORKSPACE,
                            "resource_name": WORKSPACE_WITH_READ_ONLY_ACCESS,
                            "privileges": [GRANTS_FOR_READ_ONLY],
                        },
                    ]
                ),
            }
        )
        await ops_test.model.integrate(
            f"{DATA_INTEGRATOR.charm}:mlflow", f"{CHARM_NAME}:mlflow-client"
        )

        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME, DATA_INTEGRATOR.charm], status="active", timeout=600, idle_period=60
        )

    @pytest.mark.abort_on_fail
    async def test_get_credentials_returns_correct_grants(self, ops_test: OpsTest):
        """The requirer's get-credentials action returns the provisioned workspace grants."""
        unit = ops_test.model.applications[DATA_INTEGRATOR.charm].units[0]
        action = await unit.run_action("get-credentials")
        result = await action.wait()

        assert result.results.get("ok") in (True, "True")
        mlflow_credentials = result.results["mlflow"]
        assert mlflow_credentials["username"] == TEST_IDENTITY
        assert json.loads(mlflow_credentials["grants"]) == json.dumps(
            {
                WORKSPACE_WITH_ADMIN_ACCESS_UPDATED: GRANTS_FOR_ADMIN,
                WORKSPACE_WITH_READ_ONLY_ACCESS_UPDATED: GRANTS_FOR_READ_ONLY,
            }
        )

    @pytest.mark.abort_on_fail
    async def test_configured_workspace_grants_are_defined(self, ops_test: OpsTest):
        """The configured workspace grants are defined for the test user."""
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        tracking_server_port = config["mlflow_port"]["value"]

        # while port-forwarding the tracking server for ease of access:
        with _PortForward(ops_test.model_name, tracking_server_port) as tracking_server_url:
            current_user = requests.get(
                f"{tracking_server_url}/api/2.0/mlflow/users/current",
                headers={IDENTITY_HEADER_NAME: TEST_IDENTITY},
            )
            assert current_user.status_code == 200
            assert current_user.json()["user"]["username"] == TEST_IDENTITY

            # asserting workspace grants are correctly defined:
            roles = requests.get(
                f"{tracking_server_url}/api/3.0/mlflow/users/roles/list",
                params={"username": TEST_IDENTITY},
                headers={IDENTITY_HEADER_NAME: TEST_IDENTITY},
            )
            assert roles.status_code == 200
            roles = roles.json()["roles"]
            for role in roles:
                workspace = role["workspace"]
                if workspace == WORKSPACE_WITH_ADMIN_ACCESS:
                    assert "admin" in role["permissions"]  # it might as well include others
                elif workspace == WORKSPACE_WITH_READ_ONLY_ACCESS:
                    assert role["permissions"] == ["read"]  # strictly the only one
                else:
                    assert False, f"Unexpected workspace '{workspace}' in granted roles."

    @pytest.mark.abort_on_fail
    @pytest.mark.parametrize(
        "selected_workspace,is_write_operation,expected_response_status_code",
        [
            (WORKSPACE_WITH_ADMIN_ACCESS, False, 200),
            (WORKSPACE_WITH_ADMIN_ACCESS, True, 200),
            (WORKSPACE_WITH_READ_ONLY_ACCESS, False, 200),
            (WORKSPACE_WITH_READ_ONLY_ACCESS, True, 403),
        ],
    )
    async def test_configured_workspace_grants_take_effect(
        self,
        ops_test: OpsTest,
        selected_workspace: str,
        is_write_operation: bool,
        expected_response_status_code: int,
    ):
        """The configured workspace grants take effect for the test user."""
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        tracking_server_port = config["mlflow_port"]["value"]

        # while port-forwarding the tracking server for ease of access:
        with _PortForward(ops_test.model_name, tracking_server_port) as tracking_server_url:
            request_headers = {
                UPSTREAM_WORKSPACE_HEADER_NAME: selected_workspace,
                # TODO: remove if authentication via IAM charms is implemented in integration
                # tests:
                IDENTITY_HEADER_NAME: TEST_IDENTITY,
            }
            if is_write_operation:
                response = requests.post(
                    f"{tracking_server_url}/api/2.0/mlflow/experiments/create",
                    # NOTE: the resulting experiment is actually created only once (when write
                    # requests and with admin grants), so the write request does not need to be
                    # idempotent:
                    json={"name": "experiment-to-test-grants-take-effect"},
                    headers=request_headers,
                )
            else:
                response = requests.get(
                    f"{tracking_server_url}/api/2.0/mlflow/experiments/list",
                    headers=request_headers,
                )
            assert response.status_code == expected_response_status_code, response.text

    @pytest.mark.abort_on_fail
    async def test_update_workspace_grants_of_user(self, ops_test: OpsTest):
        """Configure workspace grants for the test user by relating data-integrator."""
        await ops_test.model.applications[DATA_INTEGRATOR.charm].set_config(
            {
                "entity-name": TEST_IDENTITY,
                "entity-permissions": json.dumps(
                    [
                        {
                            "resource_type": RESOURCE_TYPE_FOR_WORKSPACE,
                            "resource_name": WORKSPACE_WITH_ADMIN_ACCESS_UPDATED,
                            "privileges": [GRANTS_FOR_ADMIN],
                        },
                        {
                            "resource_type": RESOURCE_TYPE_FOR_WORKSPACE,
                            "resource_name": WORKSPACE_WITH_READ_ONLY_ACCESS_UPDATED,
                            "privileges": [GRANTS_FOR_READ_ONLY],
                        },
                    ]
                ),
            }
        )
        await ops_test.model.integrate(
            f"{DATA_INTEGRATOR.charm}:mlflow", f"{CHARM_NAME}:mlflow-client"
        )

        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME, DATA_INTEGRATOR.charm], status="active", timeout=600, idle_period=60
        )

    @pytest.mark.abort_on_fail
    async def test_get_credentials_returns_updated_grants(self, ops_test: OpsTest):
        """The requirer's get-credentials action returns the updated workspace grants."""
        unit = ops_test.model.applications[DATA_INTEGRATOR.charm].units[0]
        action = await unit.run_action("get-credentials")
        result = await action.wait()

        assert result.results.get("ok") in (True, "True")
        mlflow_credentials = result.results["mlflow"]
        assert mlflow_credentials["username"] == TEST_IDENTITY
        assert json.loads(mlflow_credentials["grants"]) == json.dumps(
            {
                WORKSPACE_WITH_ADMIN_ACCESS_UPDATED: GRANTS_FOR_ADMIN,
                WORKSPACE_WITH_READ_ONLY_ACCESS_UPDATED: GRANTS_FOR_READ_ONLY,
            }
        )

    @pytest.mark.abort_on_fail
    async def test_updated_workspace_grants_are_defined(self, ops_test: OpsTest):
        """The updated workspace grants are defined for the test user."""
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        tracking_server_port = config["mlflow_port"]["value"]

        # while port-forwarding the tracking server for ease of access:
        with _PortForward(ops_test.model_name, tracking_server_port) as tracking_server_url:
            current_user = requests.get(
                f"{tracking_server_url}/api/2.0/mlflow/users/current",
                headers={IDENTITY_HEADER_NAME: TEST_IDENTITY},
            )
            assert current_user.status_code == 200
            assert current_user.json()["user"]["username"] == TEST_IDENTITY

            # asserting workspace grants are correctly defined:
            roles = requests.get(
                f"{tracking_server_url}/api/3.0/mlflow/users/roles/list",
                params={"username": TEST_IDENTITY},
                headers={IDENTITY_HEADER_NAME: TEST_IDENTITY},
            )
            assert roles.status_code == 200
            roles = roles.json()["roles"]
            for role in roles:
                workspace = role["workspace"]
                if workspace == WORKSPACE_WITH_ADMIN_ACCESS_UPDATED:
                    assert "admin" in role["permissions"]  # it might as well include others
                elif workspace == WORKSPACE_WITH_READ_ONLY_ACCESS_UPDATED:
                    assert role["permissions"] == ["read"]  # strictly the only one
                else:
                    assert False, f"Unexpected workspace '{workspace}' in granted roles."

    @pytest.mark.abort_on_fail
    async def test_removing_relation_revokes_workspace_grants(self, ops_test: OpsTest):
        """Removing the relation prunes the user roles, revoking the user's workspace grants."""
        await ops_test.model.applications[CHARM_NAME].remove_relation(
            "mlflow-client", f"{DATA_INTEGRATOR.charm}:mlflow"
        )
        await ops_test.model.wait_for_idle(apps=[CHARM_NAME], status="active", timeout=600)

        config = await ops_test.model.applications[CHARM_NAME].get_config()
        tracking_server_port = config["mlflow_port"]["value"]

        # while port-forwarding the tracking server for ease of access:
        with _PortForward(ops_test.model_name, tracking_server_port) as tracking_server_url:

            @retry(stop=stop_after_delay(60), wait=wait_fixed(5), reraise=True)
            def _assert_workspace_grants_revoked():
                roles = requests.get(
                    f"{tracking_server_url}/api/3.0/mlflow/users/roles/list",
                    params={"username": TEST_IDENTITY},
                    headers={IDENTITY_HEADER_NAME: TEST_IDENTITY},
                )
                assert roles.status_code == 200
                roles = roles.json()["roles"]
                for role in roles:
                    assert role["workspace"] not in (
                        WORKSPACE_WITH_ADMIN_ACCESS,
                        WORKSPACE_WITH_READ_ONLY_ACCESS,
                    )

            _assert_workspace_grants_revoked()

    @pytest.mark.abort_on_fail
    async def test_can_create_experiment_with_mlflow_library_via_port_forward(
        self, ops_test: OpsTest
    ):
        """Create an experiment with the MLflow client through kubectl port-forward."""
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = config["mlflow_port"]["value"]
        # while port-forwarding the tracking server for ease of access:
        with _PortForward(ops_test.model_name, mlflow_port) as tracking_server_url:
            response = requests.get(
                tracking_server_url,
                # TODO: remove if authentication via IAM charms is implemented in integration tests:
                headers={IDENTITY_HEADER_NAME: TEST_IDENTITY},
            )
            assert response.status_code == 200
            client = MlflowClient(tracking_uri=tracking_server_url)
            client.create_experiment(TEST_EXPERIMENT_NAME)
            all_experiments = client.search_experiments()
            assert (
                len(list(filter(lambda e: e.name == TEST_EXPERIMENT_NAME, all_experiments))) == 1
            )

    @pytest.mark.abort_on_fail
    @pytest.mark.parametrize("identity", [TEST_IDENTITY, "newly-seen-identity"])
    async def test_user_identity_and_grants_before_aliases(self, ops_test: OpsTest, identity: str):
        """Test the MLflow user's associated identity and grants before configuring aliases.

        Assert the MLflow user is always associated to the expected identity, and that it has the
        expected tenant RBAC when the identity is the one preconfigured by the charm while it has
        no grants when the identity is a newly seen one.
        """
        # port-forwarding the tracking server:
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = config["mlflow_port"]["value"]
        # while port-forwarding the tracking server for ease of access:
        with _PortForward(ops_test.model_name, mlflow_port) as tracking_server_url:
            # getting information about the current, implicitly authenticated MLflow user:
            current_user_response = requests.get(
                f"{tracking_server_url}/api/2.0/mlflow/users/current",
                # TODO: remove if authentication via IAM charms is implemented in integration tests:
                headers={IDENTITY_HEADER_NAME: identity},
            )
            assert current_user_response.status_code == 200
            current_user_username = current_user_response.json()["user"]["username"]

            # asserting the current MLflow user corresponds to the expected external identity:
            assert current_user_username == identity

            # getting roles for the current MLflow user:
            current_roles_response = requests.get(
                f"{tracking_server_url}/api/3.0/mlflow/users/roles/list",
                params={"username": current_user_username},
                # TODO: remove if authentication via IAM charms is implemented in integration tests:
                headers={IDENTITY_HEADER_NAME: identity},  # same as requested user
            )
            assert current_roles_response.status_code == 200
            user_roles = current_roles_response.json()["roles"]

            # when the identity is the test identity the charm preconfigured:
            if identity == TEST_IDENTITY:
                # asserting the MLflow user is granted only the expected tenants (workspaces):
                for role in user_roles:
                    assert role["workspace"] in (
                        WORKSPACE_WITH_ADMIN_ACCESS,
                        WORKSPACE_WITH_READ_ONLY_ACCESS,
                    )
            # when the identity is a newly seen one:
            else:
                # asserting the MLflow user has no grants:
                assert user_roles == []

    @pytest.mark.abort_on_fail
    async def test_configure_identity_aliases(self, ops_test: OpsTest):
        """Test that the charm gets active after configuring valid identity aliases."""
        await ops_test.model.applications[CHARM_NAME].set_config(
            # configuring a single identity alias to the one the charm preconfigured:
            {"identity_aliases": f"{TEST_IDENTITY_ALIAS}: {TEST_IDENTITY}\n"}
        )

        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=60 * 10,
            idle_period=60,
        )
        assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "active"

    @pytest.mark.abort_on_fail
    @pytest.mark.parametrize(
        "identity", [TEST_IDENTITY, TEST_IDENTITY_ALIAS, "newly-seen-identity"]
    )
    async def test_user_identity_and_grants_after_aliases(self, ops_test: OpsTest, identity: str):
        """Test the MLflow user's associated identity and grants after configuring aliases.

        Assert the MLflow user is always associated to the expected identity, and that it has the
        expected tenant RBAC when the identity is either the one preconfigured by the charm or an
        alias of its, while it has no grants when the identity is a newly seen one with no aliases.
        """
        # port-forwarding the tracking server:
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = config["mlflow_port"]["value"]
        # while port-forwarding the tracking server for ease of access:
        with _PortForward(ops_test.model_name, mlflow_port) as tracking_server_url:
            # getting information about the current, implicitly authenticated MLflow user:
            current_user_response = requests.get(
                f"{tracking_server_url}/api/2.0/mlflow/users/current",
                # TODO: remove if authentication via IAM charms is implemented in integration tests:
                headers={IDENTITY_HEADER_NAME: identity},
            )
            assert current_user_response.status_code == 200
            current_user_username = current_user_response.json()["user"]["username"]

            # asserting the current MLflow user corresponds to the expected external identity:
            if identity == TEST_IDENTITY_ALIAS:
                assert current_user_username == TEST_IDENTITY  # because aliasing another identity
            else:
                assert current_user_username == identity

            # getting roles for the current MLflow user:
            current_roles_response = requests.get(
                f"{tracking_server_url}/api/3.0/mlflow/users/roles/list",
                params={"username": current_user_username},
                # TODO: remove if authentication via IAM charms is implemented in integration tests:
                headers={IDENTITY_HEADER_NAME: identity},  # same as requested user
            )
            assert current_roles_response.status_code == 200
            user_roles = current_roles_response.json()["roles"]

            # when the identity is the test identity the charm preconfigured or an alias of its:
            if identity in (TEST_IDENTITY, TEST_IDENTITY_ALIAS):
                # asserting the MLflow user is granted only the expected tenants (workspaces):
                for role in user_roles:
                    assert role["workspace"] in (
                        WORKSPACE_WITH_ADMIN_ACCESS,
                        WORKSPACE_WITH_READ_ONLY_ACCESS,
                    )
            # when the identity is a newly seen one:
            else:
                # asserting the MLflow user has no grants:
                assert user_roles == []

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
        """Setup Istio in ambient mode to include MLflow and any subsidiary charms in the mesh.

        The tracking server is also restricted to in-mesh source principals here, so the rest of
        the ambient suite exercises the charm under that authorization policy.
        """
        # deploy charms providing the service mesh and the ingress while relating MLflow to them:
        await deploy_and_integrate_service_mesh_charms(CHARM_NAME, ops_test.model)

        # restrict the tracking server to the platform waypoint's identity (in-mesh traffic); the
        # ingress gateway is allowed separately by istio-ingress-k8s's own L4 policy:
        await ops_test.model.applications[CHARM_NAME].set_config(
            {
                "istio_waypoint_principal": _tracking_server_waypoint_principal(
                    ops_test.model_name
                ),
            }
        )

        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME],
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
                # TODO: remove if authentication via IAM charms is implemented in integration tests:
                f"-H '{IDENTITY_HEADER_NAME}: {TEST_IDENTITY}' "
                "-H 'Content-Type: application/json' -d \"$payload\" >/dev/null; "
                "curl --fail-with-body -sS --retry 30 --retry-delay 5 --retry-all-errors -G "
                # TODO: remove if authentication via IAM charms is implemented in integration tests:
                f"-H '{IDENTITY_HEADER_NAME}: {TEST_IDENTITY}' "
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
        # The s3-integrator generates random credentials, so assert the expected keys are
        # dispatched into the user namespace rather than their exact values. Because this store
        # advertises a TLS CA chain, the CA bundle is embedded too for direct client I/O.
        assert set(secret.data.keys()) == {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "ca-bundle.pem",
        }
        for value in secret.data.values():
            assert value
        # The embedded CA bundle must be a valid PEM certificate chain once base64-decoded.
        ca_bundle = base64.b64decode(secret.data["ca-bundle.pem"]).decode("utf-8")
        assert "BEGIN CERTIFICATE" in ca_bundle

        poddefaults_names = [f"{CHARM_NAME}{suffix}" for suffix in PODDEFAULTS_SUFFIXES]
        for name in poddefaults_names:
            pod_default = lightkube_client.get(PodDefault, name, namespace=profile_namespace)
            assert pod_default is not None

        # The access-minio PodDefault must wire the CA bundle into client pods so their direct
        # (non-proxy) boto3 connections trust the TLS artifact store.
        access_minio_poddefault = lightkube_client.get(
            PodDefault, f"{CHARM_NAME}-access-minio", namespace=profile_namespace
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
        assert "MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD" in env_var_names
        assert "MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD" in env_var_names

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
        proxy_multipart_env_vars = {
            "MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD": "false",
            "MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD": "false",
        }
        saved_multipart_env_vars = {key: os.environ.get(key) for key in proxy_multipart_env_vars}
        os.environ.update(proxy_multipart_env_vars)
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
            for key, value in saved_multipart_env_vars.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            mlflow_subprocess.terminate()

    @staticmethod
    def _curl_tracking_server_from_pod(namespace: str, url: str) -> tuple[int, str]:
        """Curl `url` from a throwaway pod in `namespace`; return (curl_exit_code, stderr).

        `kubectl run --rm -i` propagates the container's exit code, so this is curl's own status:
        0 when any HTTP response is received (even a 401/403 from the tracking server's auth), and
        56 ("Recv failure: Connection reset by peer") when the ztunnel denies the L4 connection.
        The exit code, stdout (with the HTTP status) and stderr are logged for diagnosis.
        """
        pod_name = f"mesh-probe-{TestCharm.generate_random_string(6)}"
        result = subprocess.run(
            [
                "kubectl",
                "-n",
                namespace,
                "run",
                pod_name,
                "--rm",
                "-i",
                "--restart=Never",
                "--image=curlimages/curl:8.8.0",
                "--command",
                "--",
                "curl",
                "-sS",
                "-o",
                "/dev/null",
                "-w",
                "http_code=%{http_code}",
                "--max-time",
                "15",
                url,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        logger.info(
            "mesh probe from namespace %s to %s: curl_exit_code=%s stdout=%r stderr=%r",
            namespace,
            url,
            result.returncode,
            result.stdout,
            result.stderr,
        )
        return result.returncode, result.stderr

    @pytest.mark.abort_on_fail
    async def test_out_of_mesh_pod_cannot_reach_tracking_server(
        self, ops_test: OpsTest, out_of_mesh_namespace: str
    ):
        """A pod outside the mesh must be denied access to the restricted tracking server.

        `istio_waypoint_principal` is set during the mesh integration, so only in-mesh source
        identities may reach the tracking server. A pod in a namespace that is not part of the mesh
        presents no identity, so the mesh must reject its connection at L4. The positive path
        (legitimate sources still reach the server) is covered by `test_ui_is_accessible` and
        `test_can_create_experiment_from_user_namespace`.
        """
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = config["mlflow_port"]["value"]
        model_name = ops_test.model_name
        tracking_url = f"http://{CHARM_NAME}.{model_name}.svc.cluster.local:{mlflow_port}/"

        logger.info(
            "probing tracking server %s from out-of-mesh namespace %s (expecting an L4 denial)",
            tracking_url,
            out_of_mesh_namespace,
        )

        # Retry to let the authorization policy reach the ztunnel and to tolerate pod startup.
        for attempt in Retrying(stop=stop_after_delay(300), wait=wait_fixed(15), reraise=True):
            with attempt:
                exit_code, stderr = self._curl_tracking_server_from_pod(
                    out_of_mesh_namespace, tracking_url
                )
                # the ztunnel resets the denied L4 connection, so curl exits 56 ("Recv failure:
                # Connection reset by peer"); anything else (0 = reached the server, or a
                # pod/kubectl error) is retried until the policy is enforced.
                assert exit_code == 56, (
                    "expected the out-of-mesh pod's connection to be reset at L4 (curl exit 56), "
                    f"but curl exited with {exit_code} (stderr: {stderr.strip()})"
                )
