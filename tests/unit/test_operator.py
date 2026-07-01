# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from unittest.mock import MagicMock, PropertyMock, patch

import botocore.exceptions
import pytest
import yaml
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
from charmed_kubeflow_chisme.pebble import update_layer
from charms.istio_ingress_k8s.v0.istio_ingress_route import (
    HTTPPathMatchType,
    IstioIngressRouteConfig,
    ProtocolType,
)
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.pebble import Service
from ops.testing import Harness
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed

from charm import MeshType, MlflowCharm

BUCKET_NAME = "mlflow"
CHARM_NAME = "mlflow-server"
DEFAULT_JUJU_APP_NAME = CHARM_NAME
MODEL_NAME = "testing"

OBJECT_STORAGE_DATA = {
    "access-key": "minio-access-key",
    "namespace": "namespace",
    "port": 1234,
    "secret-key": "minio-super-secret-key",
    "secure": True,
    "service": "service",
    "host": "host",
    "region": "region",
    "bucket": "bucket",
}

# Normalized object storage data as returned by MlflowCharm._get_object_storage_data
OBJECT_STORAGE_DATA_NORMALIZED = {
    "access-key": "minio-access-key",
    "secret-key": "minio-super-secret-key",
    "host": "minio.namespace",
    "port": 9000,
    "secure": False,
    "region": "",
    "bucket": "relation-bucket",
    "tls-ca-chain": None,
    "is_s3": True,
}

RELATIONAL_DB_DATA = {
    "database": "database",
    "host": "host",
    "username": "username",
    "password": "lorem-ipsum",
    "port": "port",
}

SECRETS_TEST_FILES = ["tests/test_data/secret.yaml.j2"]

EXPECTED_ENVIRONMENT = {
    "MLFLOW_BACKEND_STORE_URI": "mysql+pymysql://username:lorem-ipsum@host:port/mlflow",
    "MLFLOW_DEFAULT_ARTIFACT_ROOT": f"s3://{BUCKET_NAME}",
    "MLFLOW_EXPOSE_PROMETHEUS": "/metrics",
    "MLFLOW_HOST": "0.0.0.0",
    "MLFLOW_PORT": 5000,
}
EXPECTED_SERVICE = {
    "mlflow-server": Service(
        "mlflow-server",
        raw={
            "summary": "Entrypoint of mlflow-server image",
            "startup": "enabled",
            "override": "replace",
            "command": "mlflow server",
            "environment": EXPECTED_ENVIRONMENT,
        },
    )
}
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
    "namespace": MODEL_NAME,
    "port": EXPECTED_K8S_SERVICE_HTTP_PORT,
}


@pytest.fixture(scope="function")
def harness() -> Harness:
    """Create and return Harness for testing."""

    harness = Harness(MlflowCharm)

    harness.set_model_name(MODEL_NAME)

    harness.set_leader(True)

    harness.set_can_connect("mlflow-server", True)
    harness.set_can_connect("mlflow-prometheus-exporter", True)

    return harness


def add_relation(harness: harness, relation_endpoint: str) -> tuple[int, str]:
    """Add the given relation to the charm unit, using a random name for the remote application."""
    relation_provider_app_name = f"app-for-{relation_endpoint}"

    relation_id = harness.add_relation(relation_endpoint, relation_provider_app_name)

    harness.add_relation_unit(relation_id, f"{relation_provider_app_name}/0")

    return relation_id, relation_provider_app_name


def add_object_storage_to_harness(harness: Harness):
    """Helper function to handle object storage relation"""
    object_storage_data = {"_supported_versions": "- v1", "data": yaml.dump(OBJECT_STORAGE_DATA)}
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
        assert harness.charm.model.unit.status == BlockedStatus(
            "Missing object storage relation. "
            "Please relate to one of `object-storage` or `s3-credentials`."
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.MlflowCharm._get_interfaces")
    def test_get_object_storage_data_failure_bad_storage_object(
        self, _get_interfaces: MagicMock, harness: Harness
    ):
        add_object_storage_to_harness(harness)
        storage_object = MagicMock()
        storage_object.get_data.return_value = ["a"]
        _get_interfaces.return_value = {"object-storage": storage_object}
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == BlockedStatus(
            "Unexpected error with object-storage relation data - data not as expected"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch(
        "charm.MlflowCharm._ensure_bucket_exists",
        lambda *args, **kw: None,
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
    def test_resolve_bucket_name_from_relation(self, harness: Harness):
        """A bucket provided by the relation takes precedence over the config."""
        harness.update_config({"default_artifact_root": "from-config"})
        harness.begin()
        obj = {"bucket": "from-relation", "is_s3": True}
        assert harness.charm._resolve_bucket_name(obj) == "from-relation"

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_resolve_bucket_name_from_config(self, harness: Harness):
        """When the relation provides no bucket, fall back to the config option."""
        harness.update_config({"default_artifact_root": "from-config"})
        harness.begin()
        obj = {"bucket": "", "is_s3": True}
        assert harness.charm._resolve_bucket_name(obj) == "from-config"

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_resolve_bucket_name_missing(self, harness: Harness):
        """With no relation bucket and no config option, the charm blocks."""
        harness.update_config({"default_artifact_root": ""})
        harness.begin()
        obj = {"bucket": "", "is_s3": False}
        with pytest.raises(ErrorWithStatus) as exc_info:
            harness.charm._resolve_bucket_name(obj)
        assert exc_info.value.status_type(BlockedStatus)
        assert "No object storage bucket name available" in str(exc_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.S3BucketWrapper")
    @patch(
        "charm.MlflowCharm._get_object_storage_data",
        return_value=OBJECT_STORAGE_DATA_NORMALIZED,
    )
    def test_ensure_bucket_exists_when_bucket_present(
        self, _get_object_storage_data: MagicMock, s3_wrapper_cls: MagicMock, harness: Harness
    ):
        """An existing, reachable bucket is not (re)created."""
        s3_wrapper = s3_wrapper_cls.return_value
        s3_wrapper.bucket_exists.return_value = True
        harness.begin()
        harness.charm._ensure_bucket_exists()
        s3_wrapper.bucket_exists.assert_called_once_with("relation-bucket")
        s3_wrapper.create_bucket.assert_not_called()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.S3BucketWrapper")
    @patch(
        "charm.MlflowCharm._get_object_storage_data",
        return_value=OBJECT_STORAGE_DATA_NORMALIZED,
    )
    def test_ensure_bucket_exists_creates_missing_bucket(
        self, _get_object_storage_data: MagicMock, s3_wrapper_cls: MagicMock, harness: Harness
    ):
        """A missing bucket is created."""
        s3_wrapper = s3_wrapper_cls.return_value
        s3_wrapper.bucket_exists.return_value = False
        harness.begin()
        harness.charm._ensure_bucket_exists()
        s3_wrapper.create_bucket.assert_called_once_with("relation-bucket")

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.S3BucketWrapper")
    @patch(
        "charm.MlflowCharm._get_object_storage_data",
        return_value=OBJECT_STORAGE_DATA_NORMALIZED,
    )
    def test_ensure_bucket_exists_connection_error_waiting(
        self, _get_object_storage_data: MagicMock, s3_wrapper_cls: MagicMock, harness: Harness
    ):
        """A connectivity error puts the charm in a waiting state."""
        s3_wrapper = s3_wrapper_cls.return_value
        s3_wrapper.bucket_exists.side_effect = botocore.exceptions.EndpointConnectionError(
            endpoint_url="http://minio.namespace:9000"
        )
        harness.begin()
        with pytest.raises(ErrorWithStatus) as exc_info:
            harness.charm._ensure_bucket_exists()
        assert exc_info.value.status_type(WaitingStatus)
        assert "Waiting for object storage to become accessible" in str(exc_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.S3BucketWrapper")
    @patch(
        "charm.MlflowCharm._get_object_storage_data",
        return_value={**OBJECT_STORAGE_DATA_NORMALIZED, "bucket": ""},
    )
    def test_on_event_missing_bucket_sets_blocked_status(
        self, _get_object_storage_data: MagicMock, s3_wrapper_cls: MagicMock, harness: Harness
    ):
        """A missing bucket name propagates to a BlockedStatus unit status via _on_event."""
        harness.update_config({"default_artifact_root": ""})
        harness.begin()
        harness.charm._on_event(None)
        assert isinstance(harness.charm.model.unit.status, BlockedStatus)
        assert "No object storage bucket name available" in harness.charm.model.unit.status.message

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.S3BucketWrapper")
    @patch(
        "charm.MlflowCharm._get_object_storage_data",
        return_value=OBJECT_STORAGE_DATA_NORMALIZED,
    )
    def test_on_event_bucket_connection_error_sets_waiting_status(
        self, _get_object_storage_data: MagicMock, s3_wrapper_cls: MagicMock, harness: Harness
    ):
        """A connectivity error while ensuring the bucket propagates to a WaitingStatus."""
        s3_wrapper = s3_wrapper_cls.return_value
        s3_wrapper.bucket_exists.side_effect = botocore.exceptions.EndpointConnectionError(
            endpoint_url="http://minio.namespace:9000"
        )
        harness.begin()
        harness.charm._on_event(None)
        assert isinstance(harness.charm.model.unit.status, WaitingStatus)
        assert (
            "Waiting for object storage to become accessible"
            in harness.charm.model.unit.status.message
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch(
        "charm.MlflowCharm.service_environment",
        new_callable=PropertyMock,
        return_value=EXPECTED_ENVIRONMENT,
    )
    def test_update_layer_success(
        self,
        _: MagicMock,
        harness: Harness,
    ):
        harness.begin()
        update_layer(
            harness.charm._container_name,
            harness.charm.container,
            harness.charm._mlflow_server_layer,
            harness.charm.logger,
        )
        assert harness.charm.container.get_plan().services == EXPECTED_SERVICE

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.MlflowCharm._get_interfaces", lambda *args, **kw: None)
    @patch("charm.MlflowCharm._get_relational_db_data", lambda *args, **kw: RELATIONAL_DB_DATA)
    @patch("charm.MlflowCharm._get_object_storage_data")
    def test_generate_environment(
        self,
        mock_get_object_storage_data,
        harness: Harness,
    ):
        mock_get_object_storage_data.return_value = {
            **OBJECT_STORAGE_DATA_NORMALIZED,
            "bucket": "",
        }
        harness.begin()
        envs = harness.charm._generate_environment()
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
        "charm.MlflowCharm._ensure_bucket_exists",
        lambda *args, **kw: None,
    )
    @patch("charm.MlflowCharm._get_object_storage_data", return_value=OBJECT_STORAGE_DATA)
    @patch("charm.MlflowCharm._get_relational_db_data", return_value=RELATIONAL_DB_DATA)
    def test_on_event_waiting_for_exporter(
        self,
        _: MagicMock,
        __: MagicMock,
        harness: Harness,
    ):
        harness.set_can_connect("mlflow-prometheus-exporter", False)
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
        "charm.MlflowCharm._ensure_bucket_exists",
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
    @pytest.mark.parametrize(
        "has_service_mesh_relation, expected_called",
        [(True, True), (False, False)],
        ids=["with-service-mesh", "without-service-mesh"],
    )
    def test_reconcile_policy_resource_manager(
        self,
        harness: Harness,
        has_service_mesh_relation,
        expected_called,
    ):
        """Test policy reconciliation only happens when a service-mesh relation exists."""
        harness.begin()
        if has_service_mesh_relation:
            add_relation(harness, relation_endpoint=RELATION_ENDPOINT_FOR_SERVICE_MESH)

        mock_policy_manager = MagicMock()

        with patch.object(
            MlflowCharm,
            "_policy_resource_manager",
            new_callable=PropertyMock,
            return_value=mock_policy_manager,
        ):
            harness.charm._reconcile_policy_resource_manager()

        if expected_called:
            mock_policy_manager.reconcile.assert_called_once_with(
                policies=[],
                mesh_type=harness.charm._mesh.mesh_type,
                raw_policies=[harness.charm._allow_all_policy],
            )
        else:
            mock_policy_manager.reconcile.assert_not_called()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @pytest.mark.parametrize(
        "is_leader, expected_called",
        [(True, True), (False, False)],
        ids=["leader", "non-leader"],
    )
    def test_remove_authorization_policies(
        self,
        harness: Harness,
        is_leader,
        expected_called,
    ):
        """Test authorization policy removal is leader-gated before reconciliation."""
        harness.set_leader(is_leader)
        harness.begin()
        mock_policy_manager = MagicMock()

        with patch.object(
            MlflowCharm,
            "_policy_resource_manager",
            new_callable=PropertyMock,
            return_value=mock_policy_manager,
        ):
            harness.charm._remove_authorization_policies(None)

        if expected_called:
            mock_policy_manager.reconcile.assert_called_once_with(
                policies=[],
                mesh_type=MeshType.istio,
                raw_policies=[],
            )
        else:
            mock_policy_manager.reconcile.assert_not_called()

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
        "charm.MlflowCharm._ensure_bucket_exists",
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
                harness.charm.on[
                    RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE
                ].relation_changed.emit(
                    harness.charm.framework.model.get_relation(
                        RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE, relation_id
                    )
                )

            if add_sidecar_mode_ingress:
                # adding the sidecar-mode ingress relation while triggering relation events:
                relation_id, _ = add_relation(
                    harness, relation_endpoint=RELATION_ENDPOINT_FOR_INGRESS_IN_SIDECAR_MODE
                )
                harness.charm.on[
                    RELATION_ENDPOINT_FOR_INGRESS_IN_SIDECAR_MODE
                ].relation_changed.emit(
                    harness.charm.framework.model.get_relation(
                        RELATION_ENDPOINT_FOR_INGRESS_IN_SIDECAR_MODE, relation_id
                    )
                )

            if not add_ambient_mode_ingress and not add_sidecar_mode_ingress:
                # when no relation events are emitted, some other trigger is necessary:
                harness.charm.on.config_changed.emit()

            # assert:

            status = harness.charm.model.unit.status

            if add_ambient_mode_ingress and add_sidecar_mode_ingress:
                assert isinstance(status, BlockedStatus)
                assert (
                    f"Cannot have both '{RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE}' and "
                    f"'{RELATION_ENDPOINT_FOR_INGRESS_IN_SIDECAR_MODE}' relations at the same time"
                    ", remove one to unblock."
                ) in status.message

            else:
                assert isinstance(status, ActiveStatus)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch(
        "charm.MlflowCharm._ensure_bucket_exists",
        lambda *args, **kw: None,
    )
    @patch("charm.MlflowCharm._get_object_storage_data", return_value=OBJECT_STORAGE_DATA)
    @patch("charm.MlflowCharm._get_relational_db_data", return_value=RELATIONAL_DB_DATA)
    @patch("charm.MlflowCharm._get_interfaces")
    @patch("charm.ServiceMeshConsumer")
    def test_multiple_ambient_ingress_relations(
        self,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        harness: Harness,
    ):
        """Test the charm reconciles to active with more than one istio-ingress-route relation."""
        harness.begin()

        # Add more than one relation on the ambient ingress endpoint
        add_relation(harness, relation_endpoint=RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE)
        second_app = f"app-for-{RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE}-2"
        second_relation_id = harness.add_relation(
            RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE, second_app
        )
        harness.add_relation_unit(second_relation_id, f"{second_app}/0")

        # adding the relation is what triggers the charm to reconcile
        harness.charm.on[RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE].relation_changed.emit(
            harness.charm.framework.model.get_relation(
                RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE, second_relation_id
            )
        )

        # More than one relation on the istio-ingress-route endpoint must not block
        # the charm; it should reconcile all the way to active.
        assert isinstance(harness.charm.model.unit.status, ActiveStatus)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch(
        "charm.MlflowCharm._ensure_bucket_exists",
        lambda *args, **kw: None,
    )
    @patch("charm.MlflowCharm._get_object_storage_data", return_value=OBJECT_STORAGE_DATA)
    @patch("charm.MlflowCharm._get_relational_db_data", return_value=RELATIONAL_DB_DATA)
    @patch("charm.MlflowCharm._get_interfaces")
    @patch("charm.ServiceMeshConsumer")
    def test_each_istio_ingress_route_relation_receives_config(
        self,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        harness: Harness,
    ):
        """Test that every istio-ingress-route relation databag receives a valid config."""
        harness.begin()

        # add more than one relation on the ambient ingress endpoint
        first_relation_id, _ = add_relation(
            harness, relation_endpoint=RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE
        )
        second_app = f"app-for-{RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE}-2"
        second_relation_id = harness.add_relation(
            RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE, second_app
        )
        harness.add_relation_unit(second_relation_id, f"{second_app}/0")

        # trigger the ingress-ready event so the real requirer publishes the same
        # config to every istio-ingress-route relation databag
        harness.charm.ambient_mode_ingress.on.ready.emit(
            harness.charm.framework.model.get_relation(
                RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE, first_relation_id
            )
        )

        # assert each relation databag holds a valid HTTPRoute config
        for relation_id in (first_relation_id, second_relation_id):
            app_data = harness.get_relation_data(relation_id, harness.charm.app.name)
            assert "config" in app_data
            config = IstioIngressRouteConfig.model_validate_json(app_data["config"])

            assert len(config.http_routes) == 1
            http_route = config.http_routes[0]
            assert http_route.matches[0].path.type == HTTPPathMatchType.PathPrefix
            assert http_route.matches[0].path.value == EXPECTED_INGRESS_PATH_MATCHED_PREFIX
            assert http_route.backends[0].service == DEFAULT_JUJU_APP_NAME
            assert http_route.backends[0].port == EXPECTED_K8S_SERVICE_HTTP_PORT
            assert config.listeners[0].name == "http-80"

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch(
        "charm.MlflowCharm._ensure_bucket_exists",
        lambda *args, **kw: None,
    )
    @patch("charm.MlflowCharm._get_object_storage_data", return_value=OBJECT_STORAGE_DATA)
    @patch("charm.MlflowCharm._get_relational_db_data", return_value=RELATIONAL_DB_DATA)
    @patch("charm.MlflowCharm._get_interfaces")
    @patch("charm.ServiceMeshConsumer")
    @pytest.mark.parametrize("config_submission_broken", [True, False], ids=["broken", "good"])
    def test_ambient_mode_ingress_configurations(
        self,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        harness: Harness,
        config_submission_broken,
    ):
        """Test configuring the ingress is correctly handled, including possible exceptions."""
        # arrange:

        expected_status = ActiveStatus if not config_submission_broken else BlockedStatus

        harness.begin()

        # mocking the behavior of the ingress attribute of the charm according to the test case:
        with patch.object(harness.charm.ambient_mode_ingress, "submit_config") as submit_config:
            if config_submission_broken:
                submit_config.side_effect = Exception("Test case's exception!")

            # act:

            # adding the ambient-mode ingress relation:
            relation_id, _ = add_relation(
                harness, relation_endpoint=RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE
            )

            # triggering the ingress-ready event:
            harness.charm.ambient_mode_ingress.on.ready.emit(
                harness.charm.framework.model.get_relation(
                    RELATION_ENDPOINT_FOR_SERVICE_MESH, relation_id
                )
            )

            # assert:

            submit_config.assert_called_once()

            # asserting one and only one HTTPRoute is defined:
            submitted_ingress_configurations = submit_config.call_args.args[0]
            assert len(submitted_ingress_configurations.http_routes) == 1
            first_and_only_httproute = submitted_ingress_configurations.http_routes[0]

            # asserting that the first and only HTTPRoute defined holds the expected...

            # ...matches:
            assert len(first_and_only_httproute.matches) == 1
            assert (
                first_and_only_httproute.matches[0].path.value
                == EXPECTED_INGRESS_PATH_MATCHED_PREFIX
            )

            # ...filters:
            assert len(first_and_only_httproute.filters) == 1
            assert (
                first_and_only_httproute.filters[0].urlRewrite.path.value
                == EXPECTED_INGRESS_PATH_REWRITTEN_PREFIX
            )

            # ...backends:
            assert len(first_and_only_httproute.backends) == 1
            assert first_and_only_httproute.backends[0].service == DEFAULT_JUJU_APP_NAME
            assert first_and_only_httproute.backends[0].port == EXPECTED_K8S_SERVICE_HTTP_PORT

            assert isinstance(harness.charm.model.unit.status, expected_status)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch(
        "charm.MlflowCharm._ensure_bucket_exists",
        lambda *args, **kw: None,
    )
    @patch("charm.MlflowCharm._get_object_storage_data", return_value=OBJECT_STORAGE_DATA)
    @patch("charm.MlflowCharm._get_relational_db_data", return_value=RELATIONAL_DB_DATA)
    @patch("charm.MlflowCharm._get_interfaces")
    @patch("charm.ServiceMeshConsumer")
    @pytest.mark.parametrize(
        "tls_enabled, expected_port", [(False, 80), (True, 443)], ids=["no-tls", "tls"]
    )
    def test_ambient_mode_ingress_listener_port(
        self,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        harness: Harness,
        tls_enabled,
        expected_port,
    ):
        """Test the ambient ingress listener uses port 443 when TLS is enabled, else 80."""
        harness.begin()

        with patch.object(
            type(harness.charm.ambient_mode_ingress),
            "tls_enabled",
            new_callable=PropertyMock,
            return_value=tls_enabled,
        ), patch.object(harness.charm.ambient_mode_ingress, "submit_config") as submit_config:
            # adding the ambient-mode ingress relation:
            relation_id, _ = add_relation(
                harness, relation_endpoint=RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE
            )

            # triggering the ingress-ready event:
            harness.charm.ambient_mode_ingress.on.ready.emit(
                harness.charm.framework.model.get_relation(
                    RELATION_ENDPOINT_FOR_SERVICE_MESH, relation_id
                )
            )

            submit_config.assert_called_once()
            submitted_config = submit_config.call_args.args[0]
            assert len(submitted_config.listeners) == 1
            assert submitted_config.listeners[0].port == expected_port
            assert submitted_config.listeners[0].protocol == ProtocolType.HTTP
