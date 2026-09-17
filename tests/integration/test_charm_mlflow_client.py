# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the mlflow-client relation (MLflow provider, data-integrator requirer).

These tests exercise the provider side of the mlflow_client interface: they relate a
data-integrator to the tracking server and assert that the requested MLflow user is provisioned in
the requested workspace (tenant) with the requested access tier, that the requirer receives the
resulting username and grants, and that removing the relation revokes the access.
"""

import json
import logging
import subprocess
import time
from pathlib import Path

import pytest
import requests
import yaml
from charms_dependencies import DATA_INTEGRATOR, MINIO, POSTGRESQL_K8S
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_delay, wait_fixed

# TODO: remove the identity-header workaround once external authentication is wired end-to-end:
from auth_helpers import IDENTITY_HEADER_NAME  # isort:skip

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]

# header the tracking server reads to select the active workspace (tenant) for a request:
WORKSPACE_HEADER_NAME = "X-MLFLOW-WORKSPACE"

# the (user, tenant, tier) the requirer requests over the relation (matches DATA_INTEGRATOR): the
# single workspace->tier grant it configures via `entity-permissions`:
TEST_USERNAME = DATA_INTEGRATOR.config["entity-name"]
_grant = json.loads(DATA_INTEGRATOR.config["entity-permissions"])[0]
TEST_WORKSPACE = _grant["resource_name"]
TEST_TIER = _grant["privileges"][0]


def _port_forward_tracking_server(model_name: str, port: str) -> subprocess.Popen:
    """Port-forward the tracking server service to localhost and return the subprocess."""
    process = subprocess.Popen(
        [
            "kubectl",
            "-n",
            model_name,
            "port-forward",
            f"svc/{CHARM_NAME}",
            f"{port}:{port}",
        ]
    )
    time.sleep(10)  # must wait for the port-forward to be ready
    return process


async def _tracking_server_port(ops_test: OpsTest) -> str:
    config = await ops_test.model.applications[CHARM_NAME].get_config()
    return config["mlflow_port"]["value"]


class TestMlflowClient:
    @pytest.mark.abort_on_fail
    async def test_activate_tracking_server(self, ops_test: OpsTest):
        """Bring the tracking server to active by relating its backend store and artifact store."""
        await ops_test.model.deploy(
            MINIO.charm, channel=MINIO.channel, config=MINIO.config, trust=MINIO.trust
        )
        await ops_test.model.deploy(
            POSTGRESQL_K8S.charm,
            channel=POSTGRESQL_K8S.channel,
            series="jammy",
            config=POSTGRESQL_K8S.config,
            trust=POSTGRESQL_K8S.trust,
        )
        await ops_test.model.wait_for_idle(
            apps=[MINIO.charm, POSTGRESQL_K8S.charm],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )
        await ops_test.model.integrate(f"{MINIO.charm}:object-storage", CHARM_NAME)
        await ops_test.model.integrate(POSTGRESQL_K8S.charm, CHARM_NAME)
        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME], status="active", timeout=600, idle_period=60
        )
        assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "active"

    @pytest.mark.abort_on_fail
    async def test_relate_data_integrator(self, ops_test: OpsTest):
        """Deploy the requirer with a (user, tenant, tier) request and relate it to MLflow."""
        data_integrator_charm = DATA_INTEGRATOR.charm
        deploy_kwargs = {
            "application_name": DATA_INTEGRATOR.charm,
            "config": DATA_INTEGRATOR.config,
        }
        if data_integrator_charm == DATA_INTEGRATOR.charm:
            deploy_kwargs["channel"] = DATA_INTEGRATOR.channel

        await ops_test.model.deploy(data_integrator_charm, **deploy_kwargs)
        await ops_test.model.integrate(
            f"{DATA_INTEGRATOR.charm}:mlflow", f"{CHARM_NAME}:mlflow-client"
        )
        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME, DATA_INTEGRATOR.charm], status="active", timeout=600, idle_period=60
        )

    @pytest.mark.abort_on_fail
    async def test_get_credentials_returns_provisioned_identity(self, ops_test: OpsTest):
        """The requirer's get-credentials action returns the provisioned username and endpoint."""
        unit = ops_test.model.applications[DATA_INTEGRATOR.charm].units[0]
        action = await unit.run_action("get-credentials")
        result = await action.wait()

        assert result.results.get("ok") in (True, "True")
        mlflow_credentials = result.results["mlflow"]
        assert mlflow_credentials["username"] == TEST_USERNAME
        assert json.loads(mlflow_credentials["grants"]) == {TEST_WORKSPACE: TEST_TIER}

    @pytest.mark.abort_on_fail
    async def test_user_is_provisioned_with_tenant_role(self, ops_test: OpsTest):
        """The provisioned MLflow user exists and holds a charm-owned role in the tenant."""
        port = await _tracking_server_port(ops_test)
        port_forward = _port_forward_tracking_server(ops_test.model.name, port)
        try:
            current_user = requests.get(
                f"http://localhost:{port}/api/2.0/mlflow/users/current",
                headers={IDENTITY_HEADER_NAME: TEST_USERNAME},
            )
            assert current_user.status_code == 200
            assert current_user.json()["user"]["username"] == TEST_USERNAME

            roles = requests.get(
                f"http://localhost:{port}/api/3.0/mlflow/users/roles/list",
                params={"username": TEST_USERNAME},
                headers={IDENTITY_HEADER_NAME: TEST_USERNAME},
            )
            assert roles.status_code == 200
            workspaces = {role["workspace"] for role in roles.json()["roles"]}
            assert TEST_WORKSPACE in workspaces
        finally:
            port_forward.terminate()

    @pytest.mark.abort_on_fail
    async def test_edit_tier_can_create_experiment_in_tenant(self, ops_test: OpsTest):
        """The provisioned user's edit tier lets it create experiments in the tenant."""
        port = await _tracking_server_port(ops_test)
        port_forward = _port_forward_tracking_server(ops_test.model.name, port)
        try:
            response = requests.post(
                f"http://localhost:{port}/api/2.0/mlflow/experiments/create",
                json={"name": "mlflow-client-experiment"},
                headers={
                    IDENTITY_HEADER_NAME: TEST_USERNAME,
                    WORKSPACE_HEADER_NAME: TEST_WORKSPACE,
                },
            )
            assert response.status_code == 200, response.text
        finally:
            port_forward.terminate()

    @pytest.mark.abort_on_fail
    async def test_removing_relation_revokes_access(self, ops_test: OpsTest):
        """Removing the relation prunes the charm-owned role, revoking the user's tenant access."""
        await ops_test.model.applications[CHARM_NAME].remove_relation(
            "mlflow-client", f"{DATA_INTEGRATOR.charm}:mlflow"
        )
        await ops_test.model.wait_for_idle(apps=[CHARM_NAME], status="active", timeout=600)

        port = await _tracking_server_port(ops_test)
        port_forward = _port_forward_tracking_server(ops_test.model.name, port)
        try:

            @retry(stop=stop_after_delay(60), wait=wait_fixed(5), reraise=True)
            def _assert_tenant_role_pruned():
                roles = requests.get(
                    f"http://localhost:{port}/api/3.0/mlflow/users/roles/list",
                    params={"username": TEST_USERNAME},
                    headers={IDENTITY_HEADER_NAME: TEST_USERNAME},
                )
                assert roles.status_code == 200
                workspaces = {role["workspace"] for role in roles.json()["roles"]}
                assert TEST_WORKSPACE not in workspaces

            _assert_tenant_role_pruned()
        finally:
            port_forward.terminate()
