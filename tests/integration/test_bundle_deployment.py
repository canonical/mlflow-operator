import subprocess
import os
import aiohttp
import lightkube
import pytest
import time
from pytest_operator.plugin import OpsTest
from lightkube.resources.core_v1 import Service

# Environment variables
KUBEFLOW_CHANNEL = os.environ.get("KUBEFLOW_CHANNEL", "1.9/stable")  # Default to '1.9/stable' if not set
RESOURCE_DISPATCHER_CHANNEL = os.environ.get("RESOURCE_DISPATCHER_CHANNEL", "2.0/stable")  # Default to '2.0/stable' if not set

@pytest.fixture()
def lightkube_client() -> lightkube.Client:
    client = lightkube.Client(field_manager="kubeflow")
    return client

@pytest.fixture
def bundle_path() -> str:
    return os.environ.get("BUNDLE_PATH").replace("\"", "")

def run_juju_commands(bundle_path: str, kubeflow_channel: str, resource_dispatcher_channel: str):
    """Helper function to group and execute juju commands."""
    commands = [
        ["juju", "deploy", "kubeflow", f"--channel={kubeflow_channel}", "--trust"],
        ["juju", "deploy", bundle_path, "--trust"],
        ["juju", "deploy", "resource-dispatcher", f"--channel={resource_dispatcher_channel}", "--trust"],
        ["juju", "integrate", "mlflow-server:secrets", "resource-dispatcher:secrets"],
        ["juju", "integrate", "mlflow-server:pod-defaults", "resource-dispatcher:pod-defaults"],
        ["juju", "integrate", "mlflow-minio:object-storage", "kserve-controller:object-storage"],
        ["juju", "integrate", "kserve-controller:service-accounts", "resource-dispatcher:service-accounts"],
        ["juju", "integrate", "kserve-controller:secrets", "resource-dispatcher:secrets"],
        ["juju", "integrate", "mlflow-server:ingress", "istio-pilot:ingress"],
        ["juju", "integrate", "mlflow-server:dashboard-links", "kubeflow-dashboard:links"]
    ]

    # Execute all commands
    for command in commands:
        subprocess.run(command, check=True)

class TestCharm:
    @pytest.mark.abort_on_fail
    async def test_bundle_deployment_works(self, ops_test: OpsTest, lightkube_client, bundle_path):
        # Grouped juju commands
        run_juju_commands(bundle_path, KUBEFLOW_CHANNEL, RESOURCE_DISPATCHER_CHANNEL)

        # Wait for the model to become active and idle
        await ops_test.model.wait_for_idle(
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=1500,
        )

        # Verify deployment by checking the public URL
        url = get_public_url(lightkube_client, "kubeflow")
        result_status, result_text = await fetch_response(url)
        assert result_status == 200
        assert "Log in to Your Account" in result_text
        assert "Email Address" in result_text
        assert "Password" in result_text

def get_public_url(lightkube_client: lightkube.Client, bundle_name: str):
    """Extracts public url from service istio-ingressgateway-workload for EKS deployment.
    As a next step, this could be generalized in order for the above test to run in MicroK8s as well.
    """
    ingressgateway_svc = lightkube_client.get(
        Service, "istio-ingressgateway-workload", namespace=bundle_name
    )
    address = ingressgateway_svc.status.loadBalancer.ingress[0].hostname or ingressgateway_svc.status.loadBalancer.ingress[0].ip
    public_url = f"http://{address}"
    return public_url

async def fetch_response(url, headers=None):
    """Fetch provided URL and return pair - status and text (int, string)."""
    result_status = 0
    result_text = ""
    async with aiohttp.ClientSession() as session:
        async with session.get(url=url, headers=headers) as response:
            result_status = response.status
            result_text = await response.text()
    return result_status, str(result_text)
