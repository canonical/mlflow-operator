# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration test for the charm-refresh (Juju upgrade) database-migration path.

This suite verifies that refreshing the mlflow-server charm from an older published revision
(whose workload ships an older MLflow release with an older tracking-database schema) to the
locally built charm automatically migrates the tracking database schema and preserves the
previously stored experiment/run data, without manual intervention.

It is kept in a dedicated file and tox environment because it deploys from a released channel and
then refreshes in place, which is a different lifecycle from the other integration suites.
"""

import logging
import subprocess
import time
from pathlib import Path

import lightkube
import pytest
import requests
import yaml
from charmed_kubeflow_chisme.kubernetes import KubernetesResourceHandler
from charmed_kubeflow_chisme.testing.s3_integration import deploy_and_assert_s3_integrator
from charms_dependencies import MYSQL_K8S, S3_INTEGRATOR
from lightkube.generic_resource import load_in_cluster_generic_resources
from mlflow.tracking import MlflowClient
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_delay, wait_fixed

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]

PODDEFAULTS_CRD_TEMPLATE = "./tests/integration/crds/poddefaults.yaml"

# Channel of the already-published charm to deploy first. Its workload ships an older MLflow
# release (and therefore an older tracking-database schema) than the locally built charm, so the
# subsequent refresh exercises the automatic schema migration.
OLD_CHANNEL = "2.22/stable"

TEST_EXPERIMENT_NAME = "upgrade-experiment"
TEST_RUN_METRIC = "accuracy"
TEST_RUN_METRIC_VALUE = 0.42
TEST_RUN_PARAM = "epochs"
TEST_RUN_PARAM_VALUE = "10"


def deploy_k8s_resources(template_files: str):
    """Apply the given Kubernetes resource templates (e.g. the PodDefaults CRD)."""
    lightkube_client = lightkube.Client(field_manager=CHARM_NAME)
    k8s_resource_handler = KubernetesResourceHandler(
        field_manager=CHARM_NAME, template_files=template_files, context={}
    )
    load_in_cluster_generic_resources(lightkube_client)
    k8s_resource_handler.apply()


def _built_charm(ops_test: OpsTest, request) -> str:
    """Return the path to the locally built charm, building it if needed."""
    if charm_path := request.config.getoption("--charm-path"):
        return charm_path
    return str(ops_test.build_charm("."))


def _charm_resources() -> dict:
    """Return the OCI-image resources declared by the local charm's metadata."""
    return {
        "oci-image": METADATA["resources"]["oci-image"]["upstream-source"],
        "exporter-oci-image": METADATA["resources"]["exporter-oci-image"]["upstream-source"],
    }


class _PortForward:
    """Context manager wrapping a `kubectl port-forward` to the mlflow-server service."""

    def __init__(self, namespace: str, port: int):
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
                f"svc/{CHARM_NAME}",
                f"{self._port}:{self._port}",
            ]
        )
        time.sleep(10)  # must wait for the port-forward to be established
        return f"http://localhost:{self._port}"

    def __exit__(self, *exc):
        if self._process is not None:
            self._process.terminate()


@retry(stop=stop_after_delay(300), wait=wait_fixed(10), reraise=True)
def _assert_tracking_server_reachable(url: str):
    response = requests.get(url)
    assert response.status_code == 200


class TestUpgrade:
    @pytest.mark.abort_on_fail
    async def test_deploy_old_revision(self, ops_test: OpsTest):
        """Deploy the older published charm revision with its database and object storage."""
        deploy_k8s_resources([PODDEFAULTS_CRD_TEMPLATE])
        await deploy_and_assert_s3_integrator(
            ops_test.model, add_ca_chain=True, s3_integrator=S3_INTEGRATOR
        )
        await ops_test.model.deploy(
            CHARM_NAME,
            channel=OLD_CHANNEL,
            application_name=CHARM_NAME,
            trust=True,
        )
        await ops_test.model.deploy(
            MYSQL_K8S.charm,
            channel=MYSQL_K8S.channel,
            series="jammy",
            config=MYSQL_K8S.config,
            trust=MYSQL_K8S.trust,
        )
        await ops_test.model.wait_for_idle(
            apps=[S3_INTEGRATOR.charm, MYSQL_K8S.charm],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )
        await ops_test.model.integrate(
            f"{S3_INTEGRATOR.charm}:s3-credentials", f"{CHARM_NAME}:s3-credentials"
        )
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

    @pytest.mark.abort_on_fail
    async def test_populate_data_on_old_revision(self, ops_test: OpsTest):
        """Create an experiment and a run so the tracking database holds pre-upgrade data."""
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = int(config["mlflow_port"]["value"])
        with _PortForward(ops_test.model_name, mlflow_port) as url:
            _assert_tracking_server_reachable(url)
            client = MlflowClient(tracking_uri=url)
            experiment_id = client.create_experiment(TEST_EXPERIMENT_NAME)
            run = client.create_run(experiment_id)
            client.log_param(run.info.run_id, TEST_RUN_PARAM, TEST_RUN_PARAM_VALUE)
            client.log_metric(run.info.run_id, TEST_RUN_METRIC, TEST_RUN_METRIC_VALUE)
            client.set_terminated(run.info.run_id)

            experiments = client.search_experiments()
            assert [e for e in experiments if e.name == TEST_EXPERIMENT_NAME]

    @pytest.mark.abort_on_fail
    async def test_refresh_to_local_charm_migrates_and_stays_active(
        self, ops_test: OpsTest, request
    ):
        """Refresh to the locally built charm and assert it reconciles to active.

        The refresh triggers the `upgrade-charm` event, whose reconcile detects the out-of-date
        schema and runs the migration automatically. That migration issues a ``CREATE TRIGGER``
        statement (the immutability trigger added alongside the ``secrets`` table) which, with
        binary logging enabled, MySQL rejects for a user lacking global privileges. The locally
        built charm requests the ``charmed_dba`` role via ``extra_user_roles`` (granting
        SYSTEM_VARIABLES_ADMIN and TRIGGER) and, using those relation credentials, persists
        ``log_bin_trust_function_creators`` before the migration runs. The data-platform provider
        only grants extra roles on the *first* ``database_requested`` event, which already fired
        for the old revision without that role, so we remove the ``relational-db`` relation before
        refreshing and re-add it afterwards so the request fires again for the new charm. The
        pre-upgrade data is preserved because dropping the relation only deletes the scoped user,
        not the database.
        """
        await ops_test.model.applications[CHARM_NAME].remove_relation(
            "relational-db", f"{MYSQL_K8S.charm}:database"
        )
        # Wait for the charm to observe the relation is gone (`blocked`) before refreshing. We do
        # NOT wait for mysql-k8s to reach a stable idle here: on relation removal mysql-k8s can get
        # stuck flapping `executing` while it repeatedly fails to delete the old scoped user (it
        # logs "Failed to delete instance users"), so it may never satisfy an idle settle. That
        # stuck teardown of the *old* user does not affect re-adding the relation, which creates a
        # fresh scoped user (granted `charmed_dba` by the new revision's request).
        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME],
            status="blocked",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )

        charm = _built_charm(ops_test, request)
        await ops_test.model.applications[CHARM_NAME].refresh(
            path=charm, resources=_charm_resources()
        )
        # Let the refreshed charm settle before re-establishing the relation so the new revision's
        # `DatabaseRequires` (which requests `charmed_dba`) is the one that handles the
        # relation-created event and writes `extra-user-roles` to the databag.
        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME],
            status="blocked",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )

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

    @pytest.mark.abort_on_fail
    async def test_data_preserved_after_upgrade(self, ops_test: OpsTest):
        """Assert the pre-upgrade experiment and run survived the schema migration."""
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = int(config["mlflow_port"]["value"])
        with _PortForward(ops_test.model_name, mlflow_port) as url:
            _assert_tracking_server_reachable(url)
            client = MlflowClient(tracking_uri=url)

            experiments = [
                e for e in client.search_experiments() if e.name == TEST_EXPERIMENT_NAME
            ]
            assert len(experiments) == 1, "the pre-upgrade experiment did not survive the upgrade"

            runs = client.search_runs([experiments[0].experiment_id])
            assert len(runs) == 1, "the pre-upgrade run did not survive the upgrade"
            run = runs[0]
            assert run.data.params.get(TEST_RUN_PARAM) == TEST_RUN_PARAM_VALUE
            assert run.data.metrics.get(TEST_RUN_METRIC) == TEST_RUN_METRIC_VALUE
