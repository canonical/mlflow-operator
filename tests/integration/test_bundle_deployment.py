import subprocess
import os
import aiohttp
import lightkube
import pytest
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

class TestCharm:
    @pytest.mark.abort_on_fail
    async def test_bundle_deployment_works(self, ops_test: OpsTest, lightkube_client, bundle_path):
        # Step 1: Deploy Kubeflow with the specified channel
        subprocess.run(["juju", "deploy", "kubeflow", f"--channel={KUBEFLOW_CHANNEL}", "--trust"], check=True)

        # Step 2: Deploy the bundle path
        subprocess.run(["juju", "deploy", bundle_path, "--trust"], check=True)

        # Step 3: Deploy resource-dispatcher with its channel
        subprocess.run(["juju", "deploy", "resource-dispatcher", f"--channel={RESOURCE_DISPATCHER_CHANNEL}", "--trust"], check=True)

        # Step 4: Integrate mlflow-server with resource-dispatcher (secrets and pod-defaults)
        subprocess.run(["juju", "integrate", "mlflow-server:secrets", "resource-dispatcher:secrets"], check=True)
        subprocess.run(["juju", "integrate", "mlflow-server:pod-defaults", "resource-dispatcher:pod-defaults"], check=True)

        # Step 5: Integrate mlflow-minio with kserve-controller for object-storage
        subprocess.run(["juju", "integrate", "mlflow-minio:object-storage", "kserve-controller:object-storage"], check=True)

        # Step 6: Integrate kserve-controller with resource-dispatcher (service-accounts and secrets)
        subprocess.run(["juju", "integrate", "kserve-controller:service-accounts", "resource-dispatcher:service-accounts"], check=True)
        subprocess.run(["juju", "integrate", "kserve-controller:secrets", "resource-dispatcher:secrets"], check=True)

        # Step 7: Integrate mlflow-server with istio-pilot and kubeflow-dashboard
        subprocess.run(["juju", "integrate", "mlflow-server:ingress", "istio-pilot:ingress"], check=True)
        subprocess.run(["juju", "integrate", "mlflow-server:dashboard-links", "kubeflow-dashboard:links"], check=True)

        # Wait for istio-ingressgateway charm to be active and idle
        # This is required because later we'll try to fetch a response from the login url
        # using the ingress gateway service IP address (provided by the LoadBalancer)
        await ops_test.model.wait_for_idle(
            apps=["istio-ingressgateway"],
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=1500,
        )

        # Step 8: Wait for the whole bundle to become active and idle
        await ops_test.model.wait_for_idle(
            status="active",
            raise_on_blocked=False,
            raise_on_error=False,
            timeout=1500,
        )

        # Step 9: Verify deployment by checking the public URL
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
