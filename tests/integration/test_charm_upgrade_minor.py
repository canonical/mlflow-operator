# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Integration tests for the minor upgrade of the MLflow charm from 3.X to 3.Y.

This suite verifies that refreshing the MLflow charm from the latest stable version available
for the current major, that is 3.X, to the current version built from this source for the same
major, that is 3.Y, keeps the server functional and preserves data from the previous version.

NOTE: the charm automatically migrates the tracking server's database schema(s) on charm upgrades,
without manual intervention.
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
from charms_dependencies import POSTGRESQL_K8S, S3_INTEGRATOR
from lightkube.generic_resource import load_in_cluster_generic_resources
from mlflow.tracking import MlflowClient
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_delay, wait_fixed

# TODO: remove once multi-tenancy is completed:
from auth_helpers import IDENTITY_HEADER_NAME, TEST_IDENTITY  # isort:skip

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]

PODDEFAULTS_CRD_TEMPLATE = "./tests/integration/crds/poddefaults.yaml"

OLD_CHANNEL = "3.15/stable"

PREUPGRADE_EXPERIMENT_NAME = "pre-upgrade-experiment"
PREUPGRADE_RUN_METRIC = "accuracy"
PREUPGRADE_RUN_METRIC_VALUE = 0.42
PREUPGRADE_RUN_PARAM = "epochs"
PREUPGRADE_RUN_PARAM_VALUE = "10"

POSTUPGRADE_EXPERIMENT_NAME = "post-upgrade-experiment"
POSTUPGRADE_RUN_METRIC = "loss"
POSTUPGRADE_RUN_METRIC_VALUE = 0.13
POSTUPGRADE_RUN_PARAM = "batch_size"
POSTUPGRADE_RUN_PARAM_VALUE = "32"


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
    response = requests.get(
        url,
        # TODO: remove once multi-tenancy is completed:
        headers={IDENTITY_HEADER_NAME: TEST_IDENTITY},
    )
    assert response.status_code == 200


@pytest.mark.skip(reason="TODO: restore once we have something on stable for MLflow 3")
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

    @pytest.mark.abort_on_fail
    async def test_populate_data_on_old_version(self, ops_test: OpsTest):
        """Create some pre-upgrade data such as an experiment run with metrics and parameters."""
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = int(config["mlflow_port"]["value"])

        # while port-forwarding the tracking server for ease of access:
        with _PortForward(ops_test.model_name, mlflow_port) as url:
            _assert_tracking_server_reachable(url)

            client = MlflowClient(tracking_uri=url)

            experiment_id = client.create_experiment(PREUPGRADE_EXPERIMENT_NAME)

            run = client.create_run(experiment_id)

            client.log_param(run.info.run_id, PREUPGRADE_RUN_PARAM, PREUPGRADE_RUN_PARAM_VALUE)
            client.log_metric(run.info.run_id, PREUPGRADE_RUN_METRIC, PREUPGRADE_RUN_METRIC_VALUE)

            client.set_terminated(run.info.run_id)

            # asserting the experiment is actually created and retrievable:
            experiments = client.search_experiments()
            assert [e for e in experiments if e.name == PREUPGRADE_EXPERIMENT_NAME]

    @pytest.mark.abort_on_fail
    async def test_refresh_gets_active_for_successful_migrations(self, ops_test: OpsTest, request):
        """Refresh the tracking server and assert it gets active, meaning migrations succeeded."""
        # refreshing the tracking server to the charm built from the current source:
        await ops_test.model.applications[CHARM_NAME].refresh(
            path=_built_charm(ops_test, request), resources=_charm_resources()
        )

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
                experiment
                for experiment in client.search_experiments()
                if experiment.name == PREUPGRADE_EXPERIMENT_NAME
            ]

            # asserting the experiment data are not lost after the upgrade:
            assert len(preupgrade_experiments) == 1, "pre-upgrade experiment lost after upgrade"
            preupgrade_experiment = preupgrade_experiments[0]
            preupgrade_experiment_runs = client.search_runs([preupgrade_experiment.experiment_id])
            assert len(preupgrade_experiment_runs) == 1, "pre-upgrade run lost after upgrade"
            preupgrade_experiment_run = preupgrade_experiment_runs[0]
            assert (
                preupgrade_experiment_run.data.params.get(PREUPGRADE_RUN_PARAM)
                == PREUPGRADE_RUN_PARAM_VALUE
            )
            assert (
                preupgrade_experiment_run.data.metrics.get(PREUPGRADE_RUN_METRIC)
                == PREUPGRADE_RUN_METRIC_VALUE
            )

    @pytest.mark.abort_on_fail
    async def test_populate_data_on_new_version(self, ops_test: OpsTest):
        """Create some post-upgrade data such as an experiment run with metrics and parameters."""
        config = await ops_test.model.applications[CHARM_NAME].get_config()
        mlflow_port = int(config["mlflow_port"]["value"])

        # while port-forwarding the tracking server for ease of access:
        with _PortForward(ops_test.model_name, mlflow_port) as url:
            _assert_tracking_server_reachable(url)

            client = MlflowClient(tracking_uri=url)

            experiment_id = client.create_experiment(POSTUPGRADE_EXPERIMENT_NAME)

            run = client.create_run(experiment_id)

            client.log_param(run.info.run_id, POSTUPGRADE_RUN_PARAM, POSTUPGRADE_RUN_PARAM_VALUE)
            client.log_metric(
                run.info.run_id, POSTUPGRADE_RUN_METRIC, POSTUPGRADE_RUN_METRIC_VALUE
            )

            client.set_terminated(run.info.run_id)

            # asserting the newly created experiment data are persisted and retrievable:
            postupgrade_experiments = [
                experiment
                for experiment in client.search_experiments()
                if experiment.name == POSTUPGRADE_EXPERIMENT_NAME
            ]
            assert len(postupgrade_experiments) == 1, "post-upgrade experiment not created"
            postupgrade_experiment = postupgrade_experiments[0]
            postupgrade_experiment_runs = client.search_runs(
                [postupgrade_experiment.experiment_id]
            )
            assert len(postupgrade_experiment_runs) == 1, "post-upgrade run not created"
            postupgrade_experiment_run = postupgrade_experiment_runs[0]
            assert (
                postupgrade_experiment_run.data.params.get(POSTUPGRADE_RUN_PARAM)
                == POSTUPGRADE_RUN_PARAM_VALUE
            )
            assert (
                postupgrade_experiment_run.data.metrics.get(POSTUPGRADE_RUN_METRIC)
                == POSTUPGRADE_RUN_METRIC_VALUE
            )
