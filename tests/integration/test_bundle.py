import subprocess

import pytest
import requests
from pytest_operator.plugin import OpsTest
from tenacity import retry, stop_after_delay, wait_fixed

BUNDLE_PATH = "./releases/latest/edge/mlflow/bundle.yaml"
MLFLOW_APP_NAME = "mlflow-server"


@pytest.fixture
def forward_connections():
    mlflow_process = subprocess.Popen(
        ["kubectl", "-n", "kubeflow", "port-forward", "pod/mlflow-server-0", "5002:5000"]
    )

    exporter_process = subprocess.Popen(
        ["kubectl", "-n", "kubeflow", "port-forward", "pod/mlflow-server-0", "8002:8000"]
    )
    yield
    mlflow_process.terminate()
    exporter_process.terminate()


class TestCharm:
    @pytest.mark.abort_on_fail
    async def test_deploy_bundle_works(self, ops_test: OpsTest):
        subprocess.Popen(["juju", "deploy", f"{BUNDLE_PATH}", "--trust"])
        await ops_test.model.wait_for_idle(
            apps=[MLFLOW_APP_NAME],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=1500,
        )

    @retry(stop=stop_after_delay(5), wait=wait_fixed(1))
    @pytest.mark.abort_on_fail
    async def test_mlflow_connetion(self, forward_connections, ops_test: OpsTest):
        mlflow_response = requests.get("http://localhost:5002")
        exporter_response = requests.get("http://localhost:8002")

        assert mlflow_response.status_code == 200
        assert exporter_response.status_code == 200
