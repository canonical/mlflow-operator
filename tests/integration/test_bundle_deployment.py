import os

import aiohttp
import lightkube
import pytest
from lightkube.resources.core_v1 import Service
from pytest_operator.plugin import OpsTest

from .charms_dependencies import RESOURCE_DISPTCHER_REVISION

# Environment variables
KUBEFLOW_CHANNEL = os.environ.get(
    "KUBEFLOW_CHANNEL", "1.9/stable"
)  # Default to '1.9/stable' if not set
RESOURCE_DISPATCHER_CHANNEL = os.environ.get(
    "RESOURCE_DISPATCHER_CHANNEL", "2.0/stable"
)  # Default to '2.0/stable' if not set


@pytest.fixture()
def lightkube_client() -> lightkube.Client:
    client = lightkube.Client(field_manager="kubeflow")
    return client


@pytest.fixture
def bundle_path() -> str:
    return os.environ.get("BUNDLE_PATH").replace('"', "")


async def deploy_bundle(ops_test: OpsTest, bundle_path, trust: bool) -> None:
    """Deploy a bundle from file using juju CLI."""
    run_args = ["juju", "deploy", "-m", ops_test.model_full_name, f"{bundle_path}"]
    if trust:
        run_args.append("--trust")
    retcode, stdout, stderr = await ops_test.run(*run_args)
    print(stdout)
    assert retcode == 0, f"Deploy failed: {(stderr or stdout).strip()}"


class TestCharm:
    @pytest.mark.abort_on_fail
    async def test_deploy_bundles_and_resource_dispatcher(
        self, ops_test: OpsTest, lightkube_client, bundle_path
    ):
        """
        Deploy the Kubeflow bundle, a custom bundle from the given bundle path,
        and the resource-dispatcher charm. Then, integrate the components
        and wait for the model to become active and idle.
        """
        # Deploy Kubeflow with channel and trust
        await ops_test.model.deploy(
            entity_url="kubeflow",
            channel=KUBEFLOW_CHANNEL,
            trust=True,
        )

        # Deploy the bundle path
        await deploy_bundle(ops_test, bundle_path, trust=True)

        # Deploy resource-dispatcher with its channel and trust
        await ops_test.model.deploy(
            entity_url="resource-dispatcher",
            channel=RESOURCE_DISPATCHER_CHANNEL,
            trust=True,
            revision=RESOURCE_DISPTCHER_REVISION,
        )

        # Relate services as per Juju integrations
        await ops_test.model.relate("mlflow-server:secrets", "resource-dispatcher:secrets")
        await ops_test.model.relate(
            "mlflow-server:pod-defaults", "resource-dispatcher:pod-defaults"
        )
        await ops_test.model.relate(
            "mlflow-minio:object-storage", "kserve-controller:object-storage"
        )
        await ops_test.model.relate(
            "kserve-controller:service-accounts", "resource-dispatcher:service-accounts"
        )
        await ops_test.model.relate("kserve-controller:secrets", "resource-dispatcher:secrets")
        await ops_test.model.relate("mlflow-server:ingress", "istio-pilot:ingress")
        await ops_test.model.relate("mlflow-server:dashboard-links", "kubeflow-dashboard:links")

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
    """Extracts public URL from service istio-ingressgateway-workload."""
    ingressgateway_svc = lightkube_client.get(
        Service, "istio-ingressgateway-workload", namespace=bundle_name
    )
    address = (
        ingressgateway_svc.status.loadBalancer.ingress[0].hostname
        or ingressgateway_svc.status.loadBalancer.ingress[0].ip
    )
    public_url = f"http://{address}"
    return public_url


async def fetch_response(url, headers=None):
    """Fetch provided URL and return (status, text)."""
    result_status = 0
    result_text = ""
    async with aiohttp.ClientSession() as session:
        async with session.get(url=url, headers=headers) as response:
            result_status = response.status
            result_text = await response.text()
    return result_status, str(result_text)
