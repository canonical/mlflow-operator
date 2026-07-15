# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the major upgrade from 2.22 to 3.X.

This suite verifies that refreshing the tracking server from the latest stable version available
for the old major, that is 2.22, to the current version available for the new major, that is 3.X,
built from this source, keeps the server functional and preserves data from the previous version.

NOTE: the new major of the charm automatically migrates the tracking database schema on charm
upgrades, without manual intervention. Manual intervention is however required to recreate the
integration with MySQL that provides the backend store, as permission changes are otherwise not
picked up by the MySQL charm. This manual step is only necessary as long as the upstream bug that
requires the MLflow charm to access the database with elevated privileges is open:
https://github.com/mlflow/mlflow/issues/19943

NOTE: for the artifact store, the older MinIO (`object-storage`) integration is used rather than
the newer s3-integrator (`s3-credentials`) one as it is the only one supported by the older charm
version.
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
from charms_dependencies import MINIO, MYSQL_K8S
from lightkube.generic_resource import load_in_cluster_generic_resources
from mlflow.tracking import MlflowClient
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_delay, wait_fixed

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]

PODDEFAULTS_CRD_TEMPLATE = "./tests/integration/crds/poddefaults.yaml"

OLD_CHANNEL = "2.22/stable"

EXPERIMENT_NAME = "upgrade-experiment"
RUN_METRIC = "accuracy"
RUN_METRIC_VALUE = 0.42
RUN_PARAM = "epochs"
RUN_PARAM_VALUE = "10"


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
    """Context manager wrapping a `kubectl port-forward` to the tracking server's K8s Service."""

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
        time.sleep(10)  # waiting for the port-forwarding to be established
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
    async def test_deploy_old_version(self, ops_test: OpsTest):
        """Deploy the older charm version with its backend and artifact stores."""
        deploy_k8s_resources([PODDEFAULTS_CRD_TEMPLATE])

        await ops_test.model.deploy(
            CHARM_NAME,
            channel=OLD_CHANNEL,
            application_name=CHARM_NAME,
            trust=True,
        )
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

    @pytest.mark.abort_on_fail
    async def test_populate_data_on_old_version(self, ops_test: OpsTest):
        """Create some pre-upgrade data such as an experiment run with metrics and parameters."""
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = int(config["mlflow_port"]["value"])

        # while port-forwarding the tracking server for ease of access:
        with _PortForward(ops_test.model_name, mlflow_port) as url:
            _assert_tracking_server_reachable(url)

            client = MlflowClient(tracking_uri=url)

            experiment_id = client.create_experiment(EXPERIMENT_NAME)

            run = client.create_run(experiment_id)

            client.log_param(run.info.run_id, RUN_PARAM, RUN_PARAM_VALUE)
            client.log_metric(run.info.run_id, RUN_METRIC, RUN_METRIC_VALUE)

            client.set_terminated(run.info.run_id)

            # asserting the experiment is actually created and retrievable:
            experiments = client.search_experiments()
            assert [e for e in experiments if e.name == EXPERIMENT_NAME]

    @pytest.mark.abort_on_fail
    async def test_refresh_gets_active_for_successful_migrations(self, ops_test: OpsTest, request):
        """Refresh the tracking server and assert it gets active, meaning migrations succeeded.

        NOTE: the new major of the charm automatically migrates the tracking database schema on
        charm upgrades, without manual intervention. Manual intervention is however required to
        recreate the integration with MySQL that provides the backend store, as permission changes
        are otherwise not picked up by the MySQL charm. This manual step is only necessary as long
        as the upstream bug that requires the MLflow charm to access the database with elevated
        privileges is open: https://github.com/mlflow/mlflow/issues/19943
        """
        # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
        # TODO: remove this block delimited by "- - -" once this issue is fixed:
        # https://github.com/mlflow/mlflow/issues/19943

        # removing the relation for backend store with MySQL:
        await ops_test.model.applications[CHARM_NAME].remove_relation(
            "relational-db", f"{MYSQL_K8S.charm}:database"
        )

        # waiting for MLflow to observe the relation is gone (which triggers a blocked status):
        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME],
            status="blocked",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )
        # NOTE: waiting for MySQL to reach a stable idle is not desirable, as on relation removal
        # MySQL can get stuck flapping "executing" while it repeatedly fails to delete the old
        # scoped user (logging "Failed to delete instance users") - which is nevertheless not a
        # problem for MLflow, since the stuck teardown of the old user does not affect re-adding
        # the relation, which creates a fresh scoped user granted the newly requested privileges

        # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

        # refreshing the tracking server to the charm built from the current source:
        await ops_test.model.applications[CHARM_NAME].refresh(
            path=_built_charm(ops_test, request), resources=_charm_resources()
        )

        # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -
        # TODO: remove this block delimited by "- - -" once this issue is fixed:
        # https://github.com/mlflow/mlflow/issues/19943

        # waiting for the tracking server to settle before re-establishing the (updated) relation
        # for backend store with MySQL:
        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME],
            status="blocked",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=600,
        )

        # re-establishing the relation for backend store with MySQL:
        await ops_test.model.integrate(MYSQL_K8S.charm, CHARM_NAME)

        # - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - - -

        # waiting for the tracking server to be active:
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
        """Assert the pre-upgrade experiment data survived the schema migration."""
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = int(config["mlflow_port"]["value"])

        # while port-forwarding the tracking server for ease of access:
        with _PortForward(ops_test.model_name, mlflow_port) as url:
            _assert_tracking_server_reachable(url)
            client = MlflowClient(tracking_uri=url)

            preupgrade_experiments = [
                experiment for experiment in client.search_experiments()
                if experiment.name == EXPERIMENT_NAME
            ]

            # asserting the experiment data are not lost after the upgrade:
            assert len(preupgrade_experiments) == 1, "pre-upgrade experiment lost after upgrade"
            preupgrade_experiment = preupgrade_experiments[0]
            preupgrade_experiment_runs = client.search_runs([preupgrade_experiment.experiment_id])
            assert len(preupgrade_experiment_runs) == 1, "pre-upgrade run lost after upgrade"
            preupgrade_experiment_run = preupgrade_experiment_runs[0]
            assert preupgrade_experiment_run.data.params.get(RUN_PARAM) == RUN_PARAM_VALUE
            assert preupgrade_experiment_run.data.metrics.get(RUN_METRIC) == RUN_METRIC_VALUE
