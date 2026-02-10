# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from contextlib import nullcontext
from unittest.mock import MagicMock, patch

import pytest
import yaml
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus, GenericCharmRuntimeError
from ops.model import ActiveStatus, BlockedStatus, ErrorStatus, WaitingStatus
from ops.pebble import ChangeError, Service
from ops.testing import Harness
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed

from charm import MlflowCharm

EXPECTED_SERVICE = {
    "mlflow-server": Service(
        "mlflow-server",
        raw={
            "summary": "Entrypoint of mlflow-server image",
            "startup": "enabled",
            "override": "replace",
            "command": "mlflow server --host 0.0.0.0 --port 5000 --backend-store-uri test --default-artifact-root s3:/// --expose-prometheus /metrics",  # noqa: E501
            "environment": {"MLFLOW_TRACKING_URI": "test"},
        },
    )
}
BUCKET_NAME = "mlflow"
CHARM_NAME = "mlflow-server"
DEFAULT_JUJU_APP_NAME = CHARM_NAME

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
    "username": "username",
    "password": "lorem-ipsum",
    "port": "port",
}

EXPECTED_ENVIRONMENT = {
    "AWS_ACCESS_KEY_ID": "minio-access-key",
    "AWS_ENDPOINT_URL": "http://service.namespace:1234",
    "AWS_SECRET_ACCESS_KEY": "minio-super-secret-key",
    "DB_ROOT_PASSWORD": "lorem-ipsum",
    "MLFLOW_S3_ENDPOINT_URL": "http://service.namespace:1234",
    "MLFLOW_TRACKING_URI": "mysql+pymysql://username:lorem-ipsum@host:port/mlflow",
    "USE_SSL": "true",
}

SECRETS_TEST_FILES = ["tests/test_data/secret.yaml.j2"]

EXPECTED_INGRESS_PATH_MATCHED_PREFIX = "/mlflow/"
EXPECTED_INGRESS_PATH_REWRITTEN_PREFIX = "/"
EXPECTED_K8S_SERVICE_HTTP_PORT = 5000
RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE = "istio-ingress-route"
RELATION_ENDPOINT_FOR_INGRESS_IN_SIDECAR_MODE = "ingress"
RELATION_ENDPOINT_FOR_SERVICE_MESH = "service-mesh"

INGRESS_DATA = {
    "prefix": EXPECTED_INGRESS_PATH_MATCHED_PREFIX,
    "rewrite": EXPECTED_INGRESS_PATH_REWRITTEN_PREFIX,
    "service": DEFAULT_JUJU_APP_NAME,
    "namespace": None,
    "port": EXPECTED_K8S_SERVICE_HTTP_PORT,
}


class _FakeChangeError(ChangeError):
    """Used to simulate a ChangeError during testing."""

    def __init__(self, err, change):
        super().__init__(err, change)


@pytest.fixture(scope="function")
def harness() -> Harness:
    """Create and return Harness for testing."""

    harness = Harness(MlflowCharm)

    harness.set_leader(True)

    # setup container networking simulation
    harness.set_can_connect("mlflow-server", True)

    return harness


def add_relation(harness: harness, relation_endpoint: str) -> tuple[int, str]:
    """Add the given relation to the charm unit, using a random name for the remote application."""
    relation_provider_app_name = f"app-for-{relation_endpoint}"

    relation_id = harness.add_relation(relation_endpoint, relation_provider_app_name)

    harness.add_relation_unit(relation_id, f"{relation_provider_app_name}/0")

    return relation_id, relation_provider_app_name


def enable_exporter_container(harness: harness) -> Harness:
    """Enable mlflow-prometheus-exporter for connections."""
    harness.set_can_connect("mlflow-prometheus-exporter", True)
    return harness


def add_object_storage_to_harness(harness: Harness):
    """Helper function to handle object storage relation"""
    object_storage_data = {"_supported_versions": "- v1", "data": yaml.dump(OBJECT_STORAGE_DATA)}
    harness.set_leader(True)
    object_storage_relation_id, remote_app_name = add_relation(
        harness, relation_endpoint="object-storage"
    )
    harness.update_relation_data(object_storage_relation_id, remote_app_name, object_storage_data)
    return harness


class TestCharm:
    """Test class for TrainingOperatorCharm."""

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_log_forwarding(self, harness: Harness):
        with patch("charm.LogForwarder") as mock_logging:
            harness.begin()
            mock_logging.assert_called_once_with(charm=harness.charm)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_check_leader_failure(self, harness: Harness):
        harness.set_leader(False)
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == WaitingStatus("Waiting for leadership")

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_check_leader_success(self, harness: Harness):
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status != WaitingStatus("Waiting for leadership")

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def tests_on_pebble_ready_failure(self, harness: Harness):
        harness.set_can_connect("mlflow-server", False)
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
        get_interfaces.side_effect = NoVersionsListed(relation)
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
        get_interfaces.side_effect = NoCompatibleVersions(relation_error, [], [])
        harness.begin()
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_interfaces()

        assert e_info.value.status_type(BlockedStatus)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_interfaces_success(self, harness: Harness):
        harness = add_object_storage_to_harness(harness)
        harness.begin()
        interfaces = harness.charm._get_interfaces()
        assert interfaces["object-storage"] is not None

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.MlflowCharm._get_interfaces")
    def test_get_object_storage_data_failure_missing_storage_object(
        self, _get_interfaces: MagicMock, harness: Harness
    ):
        _get_interfaces.return_value = {"object-storage": ""}
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == WaitingStatus(
            "Waiting for object-storage relation data"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.MlflowCharm._get_interfaces")
    def test_get_object_storage_data_failure_bad_storage_object(
        self, _get_interfaces: MagicMock, harness: Harness
    ):
        storage_object = MagicMock()
        storage_object.get_data.return_value = ["a"]
        _get_interfaces.return_value = {"object-storage": storage_object}
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == BlockedStatus(
            "Unexpected error unpacking object storage data - data format not as expected. "
            "Caught exception: ''list' object has no attribute 'values''"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_object_storage_data_success(self, harness: Harness):
        harness = add_object_storage_to_harness(harness)
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == BlockedStatus(
            "Please add relation to the database"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_relational_db_data_success(self, harness: Harness):
        database = MagicMock()
        fetch_relation_data = MagicMock()
        fetch_relation_data.return_value = {
            "test-db-data": {
                "endpoints": "host:port",
                "username": "username",
                "password": "password",
            }
        }
        database.fetch_relation_data = fetch_relation_data
        harness.model.get_relation = MagicMock()
        harness.begin()
        harness.charm.database = database
        res = harness.charm._get_relational_db_data()
        assert res == {
            "host": "host",
            "password": "password",
            "port": "port",
            "username": "username",
        }

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_relational_db_data_failure_wrong_data(self, harness: Harness):
        """Test with missing username and password in databag"""
        database = MagicMock()
        fetch_relation_data = MagicMock()
        fetch_relation_data.return_value = {"test-db-data": {"endpoints": "host:port"}}
        database.fetch_relation_data = fetch_relation_data
        harness.model.get_relation = MagicMock()
        harness.begin()
        harness.charm.database = database
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_relational_db_data()
        assert e_info.value.status_type(WaitingStatus)
        assert "Incorrect data found in relation relational-db" in str(e_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_relational_db_data_failure_waiting(self, harness: Harness):
        database = MagicMock()
        fetch_relation_data = MagicMock()
        fetch_relation_data.return_value = {}
        database.fetch_relation_data = fetch_relation_data
        harness.begin()
        harness.charm.database = database
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_relational_db_data()

        assert e_info.value.status_type(BlockedStatus)
        assert "Please add relation to the database" in str(e_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.validate_s3_bucket_name")
    def test_validate_default_s3_bucket_failure_invalid_bucket(
        self, validate_s3_bucket_name: MagicMock, harness: Harness
    ):
        validate_s3_bucket_name.return_value = False
        harness.begin()
        with pytest.raises(ErrorWithStatus) as exc_info:
            harness.charm._validate_default_s3_bucket_name_and_access(BUCKET_NAME, None)
        assert "Invalid value for config default_artifact_root" in str(exc_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.validate_s3_bucket_name")
    def test_validate_default_s3_bucket_success_bucket_not_accessible(
        self,
        validate_s3_bucket_name: MagicMock,
        harness: Harness,
    ):
        s3_wrapper = MagicMock()
        s3_wrapper.check_if_bucket_accessible.return_value = False
        validate_s3_bucket_name.return_value = True
        harness.begin()
        value = harness.charm._validate_default_s3_bucket_name_and_access(BUCKET_NAME, s3_wrapper)
        assert not value

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.validate_s3_bucket_name")
    def test_validate_default_s3_bucket_success_bucket_accessible(
        self,
        validate_s3_bucket_name: MagicMock,
        harness: Harness,
    ):
        s3_wrapper = MagicMock()
        s3_wrapper.check_if_bucket_accessible.return_value = True
        validate_s3_bucket_name.return_value = True
        harness.begin()
        value = harness.charm._validate_default_s3_bucket_name_and_access(BUCKET_NAME, s3_wrapper)
        assert value

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.validate_s3_bucket_name")
    def test_validate_default_s3_bucket_failure_wrong_name(
        self, validate_s3_bucket_name: MagicMock, harness: Harness
    ):
        validate_s3_bucket_name.return_value = False
        harness.begin()
        with pytest.raises(ErrorWithStatus) as exc_info:
            harness.charm._validate_default_s3_bucket_name_and_access(BUCKET_NAME, None)
        assert exc_info.value.status_type(WaitingStatus)
        assert "Invalid value for config default_artifact_root" in str(exc_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_validate_default_s3_bucket_failure_bucket_creation_not_allowed(
        self,
        harness: Harness,
    ):
        harness.update_config({"create_default_artifact_root_if_missing": False})
        s3_wrapper = MagicMock()
        check_if_bucket_accessible = MagicMock()
        check_if_bucket_accessible.return_value = False
        s3_wrapper.check_if_bucket_accessible = check_if_bucket_accessible
        harness.begin()
        with pytest.raises(ErrorWithStatus) as exc_info:
            harness.charm._validate_default_s3_bucket_name_and_access(BUCKET_NAME, s3_wrapper)

        assert exc_info.value.status_type(BlockedStatus)
        assert "Error with default S3 artifact store - " in str(exc_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.MlflowCharm.container")
    def test_update_layer_failure_container_problem(
        self,
        container: MagicMock,
        harness: Harness,
    ):
        change = MagicMock()
        change.tasks = []
        container.replan.side_effect = _FakeChangeError("Fake problem during layer update", change)
        harness.begin()
        with pytest.raises(ErrorWithStatus) as exc_info:
            harness.charm._update_layer(container, harness.charm._container_name, MagicMock())

        assert exc_info.value.status_type(BlockedStatus)
        assert "Failed to replan with error: " in str(exc_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_update_layer_success(
        self,
        harness: Harness,
    ):
        harness.begin()
        harness.charm._update_layer(
            harness.charm.container,
            harness.charm._container_name,
            harness.charm._charmed_mlflow_layer({"MLFLOW_TRACKING_URI": "test"}, ""),
        )
        assert harness.charm.container.get_plan().services == EXPECTED_SERVICE

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_env_vars(
        self,
        harness: Harness,
    ):
        harness.begin()
        envs = harness.charm._get_env_vars(RELATIONAL_DB_DATA, OBJECT_STORAGE_DATA)
        assert envs == EXPECTED_ENVIRONMENT

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_create_manifests(self, harness: Harness):
        secrets_context = {
            "access_key": "a",
            "secret_access_key": "s",
        }
        harness.begin()
        manifests_items = harness.charm._create_manifests(SECRETS_TEST_FILES, secrets_context)
        manifests_as_json = json.dumps([item.manifest for item in manifests_items])
        assert (
            manifests_as_json
            == '[{"apiVersion": "v1", "kind": "Secret", "metadata": {"name": "mlpipeline-minio-artifact"}, "stringData": {"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "s"}}]'  # noqa: E501
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.MlflowCharm._create_manifests")
    @patch("charm.MlflowCharm.secrets_manifests_wrapper")
    def test_send_manifests(
        self, secrets_manifests_wrapper: MagicMock, create_manifests: MagicMock, harness: Harness
    ):
        tmp_manifests = "[]"
        create_manifests.return_value = tmp_manifests
        secrets_manifests_wrapper = MagicMock()
        harness.begin()
        harness.charm._send_manifests({}, [""], secrets_manifests_wrapper)
        secrets_manifests_wrapper.send_data.assert_called_once()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch(
        "charm.MlflowCharm._validate_default_s3_bucket_name_and_access", lambda *args, **kw: True
    )
    @patch(
        "charm.S3BucketWrapper.__init__",
        lambda *args, **kw: None,
    )
    @patch("charm.MlflowCharm._get_object_storage_data", return_value=OBJECT_STORAGE_DATA)
    @patch("charm.MlflowCharm._get_relational_db_data", return_value=RELATIONAL_DB_DATA)
    def test_on_event_wainting_for_exporter(
        self,
        _: MagicMock,
        __: MagicMock,
        harness: Harness,
    ):
        harness.begin()
        harness.charm._on_event(None)
        assert harness.charm.model.unit.status == WaitingStatus(
            "Container mlflow-prometheus-exporter is not ready"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch(
        "charm.MlflowCharm._validate_default_s3_bucket_name_and_access", lambda *args, **kw: True
    )
    @patch(
        "charm.S3BucketWrapper.__init__",
        lambda *args, **kw: None,
    )
    @patch("charm.MlflowCharm._get_object_storage_data", return_value=OBJECT_STORAGE_DATA)
    @patch("charm.MlflowCharm._get_relational_db_data", return_value=RELATIONAL_DB_DATA)
    def test_on_event(
        self,
        _: MagicMock,
        __: MagicMock,
        harness: Harness,
    ):
        harness = enable_exporter_container(harness)
        harness.set_leader(True)
        harness.begin()
        harness.charm._on_event(None)
        assert harness.charm.model.unit.status == ActiveStatus()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_on_database_relation_removed(
        self,
        harness: Harness,
    ):
        harness.begin()
        harness.charm._on_database_relation_removed(None)
        assert harness.charm.model.unit.status == BlockedStatus(
            "Please add relation to the database"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_on_get_minio_credentials_failure(self, harness: Harness):
        event = MagicMock()
        harness.begin()
        harness.charm._on_get_minio_credentials(event)
        event.fail.assert_called_with(
            "Minio is not reachable yet. Please try again in a few minutes."
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_on_get_minio_credentials_success(self, harness: Harness):
        harness = add_object_storage_to_harness(harness)
        event = MagicMock()
        harness.begin()
        harness.charm._on_get_minio_credentials(event)
        event.set_results.assert_called_with(
            {
                "access-key": OBJECT_STORAGE_DATA["access-key"],
                "secret-access-key": OBJECT_STORAGE_DATA["secret-key"],
            }
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_send_ingress_info_success(self, harness: Harness):
        harness.begin()
        ingress = MagicMock()
        interfaces = {"ingress": ingress}
        harness.charm._send_ingress_info(interfaces)
        ingress.send_data.assert_called_with(INGRESS_DATA)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch(
        "charm.MlflowCharm._validate_default_s3_bucket_name_and_access", lambda *args, **kw: True
    )
    @patch(
        "charm.S3BucketWrapper.__init__",
        lambda *args, **kw: None,
    )
    @patch("charm.MlflowCharm._get_object_storage_data", return_value=OBJECT_STORAGE_DATA)
    @patch("charm.MlflowCharm._get_relational_db_data", return_value=RELATIONAL_DB_DATA)
    @patch("charm.MlflowCharm._get_interfaces")
    @patch("charm.ServiceMeshConsumer")
    @pytest.mark.parametrize(
        "add_ambient_mode_ingress", [True, False], ids=["ambient", "no-ambient"]
    )
    @pytest.mark.parametrize(
        "add_sidecar_mode_ingress", [True, False], ids=["sidecar", "no-sidecar"]
    )
    def test_istio_relations_conflict_detector(
        self,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        harness: Harness,
        add_ambient_mode_ingress,
        add_sidecar_mode_ingress,
    ):
        """Test the status based on conflicting ingress relations."""
        # arrange:

        harness.begin()

        with patch.object(harness.charm, "_on_ambient_mode_ingress_ready"):
            # act:

            if add_ambient_mode_ingress:
                # adding the ambient-mode ingress relation while triggering relation events:
                relation_id, _ = add_relation(
                    harness, relation_endpoint=RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE
                )
                harness.charm.on[RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE].relation_changed.emit(
                    harness.charm.framework.model.get_relation(
                        RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE, relation_id
                    )
                )

            if add_sidecar_mode_ingress:
                # adding the sidecar-mode ingress relation while triggering relation events:
                relation_id, _ = add_relation(
                    harness, relation_endpoint=RELATION_ENDPOINT_FOR_INGRESS_IN_SIDECAR_MODE
                )
                harness.charm.on[RELATION_ENDPOINT_FOR_INGRESS_IN_SIDECAR_MODE].relation_changed.emit(
                    harness.charm.framework.model.get_relation(
                        RELATION_ENDPOINT_FOR_INGRESS_IN_SIDECAR_MODE, relation_id
                    )
                )

            # assert:

            status = harness.charm.model.unit.status

            if add_ambient_mode_ingress and add_sidecar_mode_ingress:
                assert isinstance(status, BlockedStatus)
                assert (
                    f"Cannot have both '{RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE}' and "
                    f"'{RELATION_ENDPOINT_FOR_INGRESS_IN_SIDECAR_MODE}' relations at the same time, "
                    "remove one to unblock."
                ) in status.message

            else:
                assert isinstance(status, ActiveStatus)

    # @patch(
    #     "charm.KubernetesServicePatch",
    #     lambda x, y, service_name, service_type, refresh_event: None,
    # )
    # @patch(
    #     "charm.MlflowCharm._validate_default_s3_bucket_name_and_access", lambda *args, **kw: True
    # )
    # @patch(
    #     "charm.S3BucketWrapper.__init__",
    #     lambda *args, **kw: None,
    # )
    # @patch("charm.MlflowCharm._get_object_storage_data", return_value=OBJECT_STORAGE_DATA)
    # @patch("charm.MlflowCharm._get_relational_db_data", return_value=RELATIONAL_DB_DATA)
    # @patch("charm.MlflowCharm._get_interfaces")
    # @patch("charm.IstioIngressRouteRequirer.submit_config")
    # @patch("charm.ServiceMeshConsumer")
    # @pytest.mark.parametrize("config_submission_broken", [True, False], ids=["broken", "good"])
    # @pytest.mark.parametrize("is_ingress_ready", [True, False], ids=["ready", "not-ready"])
    # @pytest.mark.parametrize("is_unit_leader", [True, False], ids=["leader", "non-leader"])
    # def test_ambient_mode_ingress_configurations(
    #     self,
    #     _: MagicMock,
    #     __: MagicMock,
    #     ___: MagicMock,
    #     ____: MagicMock,
    #     _____: MagicMock,
    #     harness: Harness,
    #     config_submission_broken,
    #     is_ingress_ready,
    #     is_unit_leader,
    # ):
    #     """Test configuring the ingress is correctly handled based on leadership and errors."""
    #     # arrange:

    #     expected_status = ActiveStatus if not config_submission_broken else ErrorStatus

    #     harness.begin()

    #     relation_id, _ = add_relation(
    #         harness, relation_endpoint=RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE
    #     )

    #     # mocking the behavior of the ingress attribute of the charm according to the test case:
    #     with patch.object(harness.charm, "ambient_mode_ingress") as mocked_ingress:
    #         ingress_submit_config = mocked_ingress.submit_config
    #         if config_submission_broken:
    #             ingress_submit_config.side_effect = Exception("Test case's exception!")

    #         # act (and assert exception raised, if any):

    #         with (
    #             pytest.raises(GenericCharmRuntimeError)
    #             if config_submission_broken
    #             else nullcontext()
    #         ) as exc_info:
    #             harness.charm.on[RELATION_ENDPOINT_FOR_SERVICE_MESH].relation_changed.emit(
    #                 harness.charm.framework.model.get_relation(
    #                     RELATION_ENDPOINT_FOR_SERVICE_MESH, relation_id
    #                 )
    #             )

    #         # assert (the rest):

    #         if config_submission_broken:
    #             assert "Failed to submit ingress config: " in str(exc_info.value)

    #         if is_unit_leader and is_ingress_ready:
    #             ingress_submit_config.assert_called_once()

    #             # asserting one and only one HTTPRoute is defined:
    #             submitted_ingress_configurations = ingress_submit_config.call_args.args[0]
    #             assert len(submitted_ingress_configurations.http_routes) == 1
    #             first_and_only_httproute = submitted_ingress_configurations.http_routes[0]

    #             # asserting that the first and only HTTPRoute defined holds the expected...

    #             # ...matches:
    #             assert len(first_and_only_httproute.matches) == 1
    #             assert (
    #                 first_and_only_httproute.matches[0].path.value
    #                 == EXPECTED_INGRESS_PATH_MATCHED_PREFIX
    #             )

    #             # ...filters:
    #             assert len(first_and_only_httproute.filters) == 1
    #             assert (
    #                 first_and_only_httproute.filters[0].urlRewrite.path.value
    #                 == EXPECTED_INGRESS_PATH_REWRITTEN_PREFIX
    #             )

    #             # ...backends:
    #             assert len(first_and_only_httproute.backends) == 1
    #             assert first_and_only_httproute.backends[0].service == DEFAULT_JUJU_APP_NAME
    #             assert first_and_only_httproute.backends[0].port == EXPECTED_K8S_SERVICE_HTTP_PORT

    #         else:
    #             ingress_submit_config.assert_not_called()

    #         assert isinstance(harness.charm.model.unit.status, expected_status)
