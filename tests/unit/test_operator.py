# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, call, patch

import pytest
import yaml

from lightkube.core.exceptions import ApiError
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.testing import Harness, SIMULATE_CAN_CONNECT
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed

from charm import CharmedMlflowCharm

OBJECT_STORAGE_DATA = {
    "access-key": "minio-access-key",
    "namespace": "namespace",
    "port": 1234,
    "secret-key": "minio-super-secret-key",
    "secure": True,
    "service": "service",
}


@pytest.fixture(scope="function")
def harness() -> Harness:
    """Create and return Harness for testing."""

    harness = Harness(CharmedMlflowCharm)

    # setup container networking simulation
    harness.set_can_connect("charmed-mlflow", True)

    return harness


@pytest.fixture(scope="function")
def obejct_storage_relation(harness: Harness):
    object_storage_data = {"_supported_versions": "- v1", "data": yaml.dump(OBJECT_STORAGE_DATA)}
    harness.set_leader(True)
    object_storage_relation_id = harness.add_relation("object-storage", "storage-provider")
    harness.add_relation_unit(object_storage_relation_id, "storage-provider/0")
    harness.update_relation_data(
        object_storage_relation_id, "storage-provider", object_storage_data
    )
    return harness


class TestCharm:
    """Test class for TrainingOperatorCharm."""

    def test_check_leader_failure(self, harness: Harness):
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == WaitingStatus("Waiting for leadership")

    def test_check_leader_success(self, harness: Harness):
        harness.set_leader(True)
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status != WaitingStatus("Waiting for leadership")

    @patch("charm.get_interfaces")
    def test_get_interfaces_success(self, get_interfaces: MagicMock, harness: Harness):
        harness.set_leader(True)
        harness.begin_with_initial_hooks()
        get_interfaces.assert_called_once()

    @patch("charm.CharmedMlflowCharm._get_interfaces")
    def test_get_object_storage_data_failure_missing_storage_object(
        self, _get_interfaces: MagicMock, harness: Harness
    ):
        _get_interfaces.return_value = {"object-storage": ""}
        harness.set_leader(True)
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == WaitingStatus(
            "Waiting for object-storage relation data"
        )

    @patch("charm.CharmedMlflowCharm._get_interfaces")
    def test_get_object_storage_data_failure_bad_storage_object(
        self, _get_interfaces: MagicMock, harness: Harness
    ):
        storage_object = MagicMock()
        storage_object.get_data.return_value = ["a"]
        _get_interfaces.return_value = {"object-storage": storage_object}
        harness.set_leader(True)
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == BlockedStatus(
            "Unexpected error unpacking object storage data - data format not as expected. Caught exception: ''list' object has no attribute 'values''"
        )

    def test_get_object_storage_data_success(self, harness: Harness):
        object_storage_data = {
            "_supported_versions": "- v1",
            "data": yaml.dump(OBJECT_STORAGE_DATA),
        }
        harness.set_leader(True)
        object_storage_relation_id = harness.add_relation("object-storage", "storage-provider")
        harness.add_relation_unit(object_storage_relation_id, "storage-provider/0")
        harness.update_relation_data(
            object_storage_relation_id, "storage-provider", object_storage_data
        )
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == WaitingStatus(
            "Waiting for relational-db relation data"
        )

    def test_get_object_storage_data_success(self, obejct_storage_relation: Harness):
        obejct_storage_relation.begin_with_initial_hooks()
        assert obejct_storage_relation.charm.model.unit.status == WaitingStatus(
            "Waiting for relational-db relation data"
        )
    
    def test_get_relational_db_data_failure(self,)
