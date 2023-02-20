# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

from unittest.mock import MagicMock, patch

import pytest
import yaml
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import ChangeError
from ops.testing import Harness
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed

from charm import CharmedMlflowCharm

BUCKET_NAME = "test-bucket"
CHARM_NAME = "charmed-mlflow"

OBJECT_STORAGE_DATA = {
    "access-key": "minio-access-key",
    "namespace": "namespace",
    "port": 1234,
    "secret-key": "minio-super-secret-key",
    "secure": True,
    "service": "service",
}

RELATIONAL_DB_DATA = {
    "database": "database",
    "host": "host",
    "root_password": "lorem-ipsum",
    "port": "port",
}

EXPECTED_ENVIRONMENT = {
    "AWS_ENDPOINT_URL": "http://service.namespace:1234",
    "AWS_ACCESS_KEY_ID": "minio-access-key",
    "AWS_SECRET_ACCESS_KEY": "minio-super-secret-key",
    "USE_SSL": "true",
    "DB_ROOT_PASSWORD": "lorem-ipsum",
    "MLFLOW_TRACKING_URI": "mysql+pymysql://root:lorem-ipsum@host:port/database",
}


class _FakeNoVersionsListed(NoVersionsListed):
    def __init__(self):
        super().__init__(MagicMock())


class _FakeChangeError(ChangeError):
    """Used to simulate a ChangeError during testing."""

    def __init__(self, err, change):
        super().__init__(err, change)


@pytest.fixture(scope="function")
def harness() -> Harness:
    """Create and return Harness for testing."""

    harness = Harness(CharmedMlflowCharm)

    # setup container networking simulation
    harness.set_can_connect("charmed-mlflow", True)

    return harness


@pytest.fixture(scope="function")
def obejct_storage_relation(harness: Harness) -> Harness:
    object_storage_data = {"_supported_versions": "- v1", "data": yaml.dump(OBJECT_STORAGE_DATA)}
    harness.set_leader(True)
    object_storage_relation_id = harness.add_relation("object-storage", "storage-provider")
    harness.add_relation_unit(object_storage_relation_id, "storage-provider/0")
    harness.update_relation_data(
        object_storage_relation_id, "storage-provider", object_storage_data
    )
    return harness


@pytest.fixture(scope="function")
def relational_db_relation(obejct_storage_relation: Harness) -> Harness:
    rel_id = obejct_storage_relation.add_relation("relational-db", "mysql_app")
    obejct_storage_relation.add_relation_unit(rel_id, "mysql_app/0")
    obejct_storage_relation.update_relation_data(rel_id, "mysql_app/0", RELATIONAL_DB_DATA)
    return obejct_storage_relation


class TestCharm:
    """Test class for TrainingOperatorCharm."""

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_check_leader_failure(self, harness: Harness):
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == WaitingStatus("Waiting for leadership")

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_check_leader_success(self, harness: Harness):
        harness.set_leader(True)
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status != WaitingStatus("Waiting for leadership")

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def tests_on_pebble_ready_failure(self):
        harness = Harness(CharmedMlflowCharm)
        harness.set_can_connect("charmed-mlflow", False)
        harness.begin()
        with pytest.raises(ErrorWithStatus):
            harness.charm._on_pebble_ready(None)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def tests_on_pebble_ready_success(self, harness: Harness):
        harness.begin()
        harness.charm._on_event = MagicMock()
        harness.charm._on_pebble_ready(None)
        harness.charm._on_event.assert_called()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.get_interfaces")
    def test_get_interfaces_failure_no_versions_listed(
        self, get_interfaces: MagicMock, harness: Harness
    ):
        relation = MagicMock()
        relation.name = "A"
        relation.id = "1"
        get_interfaces.side_effect = NoVersionsListed(relation, [])
        harness.begin()
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_interfaces()

        assert e_info.value.status_type(WaitingStatus)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.get_interfaces")
    def test_get_interfaces_failure_no_compatible_versions(
        self, get_interfaces: MagicMock, harness: Harness
    ):
        relation_error = MagicMock()
        relation_error.name = "A"
        relation_error.id = "1"
        get_interfaces.side_effect = NoCompatibleVersions(relation_error, apps=[])
        harness.begin()
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_interfaces()

        assert e_info.value.status_type(BlockedStatus)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.get_interfaces")
    def test_get_interfaces_success(self, get_interfaces: MagicMock, harness: Harness):
        harness.set_leader(True)
        harness.begin_with_initial_hooks()
        get_interfaces.assert_called_once()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
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

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
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
            "Unexpected error unpacking object storage data - data format not as expected. "
            "Caught exception: ''list' object has no attribute 'values''"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_object_storage_data_success(self, obejct_storage_relation: Harness):
        obejct_storage_relation.begin_with_initial_hooks()
        assert obejct_storage_relation.charm.model.unit.status == WaitingStatus(
            "Waiting for relational-db relation data"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.CharmedMlflowCharm._validate_default_s3_bucket")
    def test_get_relational_db_data_success(
        self, validate_default_s3_bucket: MagicMock, relational_db_relation: Harness
    ):
        relational_db_relation.begin_with_initial_hooks()
        validate_default_s3_bucket.assert_called_with(OBJECT_STORAGE_DATA)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.CharmedMlflowCharm._validate_default_s3_bucket")
    def test_get_relational_db_data_failure_multiple_relations(
        self, validate_default_s3_bucket: MagicMock, relational_db_relation: Harness
    ):
        rel_id = relational_db_relation.add_relation("relational-db", "mysql_app2")
        relational_db_relation.add_relation_unit(rel_id, "mysql_app2/0")
        relational_db_relation.update_relation_data(rel_id, "mysql_app2/0", RELATIONAL_DB_DATA)
        relational_db_relation.begin_with_initial_hooks()
        assert relational_db_relation.charm.model.unit.status == BlockedStatus(
            "Too many mysql relations 2"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.S3BucketWrapper.__init__")
    @patch("charm.S3BucketWrapper.check_if_bucket_accessible")
    @patch("charm.CharmedMlflowCharm._update_layer")
    def test_validate_default_s3_bucket_success_bucket_exists(
        self,
        update_layer: MagicMock,
        check_if_bucket_accessible: MagicMock,
        init: MagicMock,
        relational_db_relation: Harness,
    ):
        check_if_bucket_accessible.return_value = True
        init.return_value = None
        relational_db_relation.begin_with_initial_hooks()
        update_layer.assert_called_with(EXPECTED_ENVIRONMENT, "mlflow")
        assert relational_db_relation.charm.model.unit.status == ActiveStatus()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.S3BucketWrapper.__init__")
    @patch("charm.S3BucketWrapper.check_if_bucket_accessible")
    @patch("charm.CharmedMlflowCharm._update_layer")
    @patch("charm.S3BucketWrapper.create_bucket")
    def test_validate_default_s3_bucket_success_bucket_created(
        self,
        _: MagicMock,
        update_layer: MagicMock,
        check_if_bucket_accessible: MagicMock,
        init: MagicMock,
        relational_db_relation: Harness,
    ):
        check_if_bucket_accessible.return_value = False
        init.return_value = None
        relational_db_relation.begin_with_initial_hooks()
        update_layer.assert_called_with(EXPECTED_ENVIRONMENT, "mlflow")
        assert relational_db_relation.charm.model.unit.status == ActiveStatus()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.validate_s3_bucket_name")
    def test_validate_default_s3_bucket_failure_wrong_name(
        self, validate_s3_bucket_name: MagicMock, relational_db_relation: Harness
    ):
        validate_s3_bucket_name.return_value = False
        relational_db_relation.begin_with_initial_hooks()
        assert relational_db_relation.charm.model.unit.status == BlockedStatus(
            "Invalid value for config default_artifact_root 'mlflow' "
            "- value must be a valid S3 bucket name"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.S3BucketWrapper.__init__")
    @patch("charm.S3BucketWrapper.check_if_bucket_accessible")
    @patch("charm.S3BucketWrapper.create_bucket")
    def test_validate_default_s3_bucket_failure_bucket_creation(
        self,
        create_bucket: MagicMock,
        check_if_bucket_accessible: MagicMock,
        init: MagicMock,
        relational_db_relation: Harness,
    ):
        check_if_bucket_accessible.return_value = False
        init.return_value = None
        create_bucket.side_effect = Exception()
        relational_db_relation.begin_with_initial_hooks()
        assert relational_db_relation.charm.model.unit.status == BlockedStatus(
            "Error with default S3 artifact store - bucket "
            "not accessible or cannot be created.  Caught error: '"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.S3BucketWrapper.__init__")
    @patch("charm.S3BucketWrapper.check_if_bucket_accessible")
    @patch("charm.S3BucketWrapper.create_bucket")
    def test_validate_default_s3_bucket_failure_bucket_creation_not_allowed(
        self,
        create_bucket: MagicMock,
        check_if_bucket_accessible: MagicMock,
        init: MagicMock,
        relational_db_relation: Harness,
    ):
        relational_db_relation.update_config({"create_default_artifact_root_if_missing": False})
        check_if_bucket_accessible.return_value = False
        init.return_value = None
        create_bucket.side_effect = Exception()
        relational_db_relation.begin_with_initial_hooks()
        assert relational_db_relation.charm.model.unit.status == BlockedStatus(
            "Error with default S3 artifact store - "
            "bucket not accessible or does not exist. "
            "Set create_default_artifact_root_if_missing=True "
            "to automatically create a missing default bucket"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.CharmedMlflowCharm.container")
    @patch("charm.CharmedMlflowCharm._validate_default_s3_bucket")
    def test_update_layer_failure_container_problem(
        self,
        _: MagicMock,
        container: MagicMock,
        relational_db_relation: Harness,
    ):
        change = MagicMock()
        change.tasks = []
        container.replan.side_effect = _FakeChangeError("Fake problem during layer update", change)
        relational_db_relation.begin_with_initial_hooks()
        assert relational_db_relation.charm.model.unit.status == BlockedStatus(
            "Failed to replan with error: Fake problem during layer update"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.CharmedMlflowCharm._validate_default_s3_bucket")
    @patch("charm.CharmedMlflowCharm._update_layer")
    def test_environament_variables(
        self,
        update_layer: MagicMock,
        validate_default_s3_bucket: MagicMock,
        relational_db_relation: Harness,
    ):
        validate_default_s3_bucket.return_value = BUCKET_NAME
        relational_db_relation.begin_with_initial_hooks()
        update_layer.assert_called_with(EXPECTED_ENVIRONMENT, BUCKET_NAME)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.CharmedMlflowCharm._validate_default_s3_bucket")
    def test_update_layer_success(
        self,
        _: MagicMock,
        relational_db_relation: Harness,
    ):
        relational_db_relation.begin_with_initial_hooks()
        assert relational_db_relation.charm.model.unit.status == ActiveStatus()
