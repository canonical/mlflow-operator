import pytest
import yaml

from pathlib import Path
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]


class TestDeployAWS:
    @pytest.mark.abort_on_fail
    async def test_001_build_and_deploy(self, ops_test: OpsTest):
        """Build and deploy the charm.

        Assert on the unit status.
        """
        await ops_test.model.deploy(CHARM_NAME, channel="latest/edge", trust=True)
        await ops_test.model.wait_for_idle(
            apps=[CHARM_NAME], status="waiting", raise_on_blocked=True, timeout=300
        )
        assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "waiting"
        assert (
            ops_test.model.applications[CHARM_NAME].units[0].workload_status_message
            == "Waiting for object-storage relation data"
        )
