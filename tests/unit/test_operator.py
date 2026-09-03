# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

import base64
import json
import logging
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
from charms.resource_dispatcher.v0.kubernetes_manifests import KUBERNETES_MANIFESTS_FIELD
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import ExecError, Service
from ops.testing import Harness
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed

from charm import (
    ENABLE_TRIGGER_CREATION_SNIPPET,
    PODDEFAULTS_FILES,
    S3_CA_BUNDLE_CONTAINER_PATH,
    SCHEMA_OUT_OF_DATE_MARKER,
    SECRETS_FILES,
    MeshType,
    MlflowCharm,
)

BUCKET_NAME = "mlflow"
CHARM_NAME = "mlflow-server"
DEFAULT_JUJU_APP_NAME = CHARM_NAME
MODEL_NAME = "testing"

CONFIG_OPTION_NAME_FOR_SERVE_ARTIFACTS = "serve_artifacts"

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

# Normalized artifact store data as returned by MlflowCharm._get_artifact_store_data
OBJECT_STORAGE_DATA_NORMALIZED = {
    "access_key": "minio-access-key",
    "secret_key": "minio-super-secret-key",
    "host": "minio.namespace",
    "port": 9000,
    "secure": False,
    "region": "",
    "bucket": "relation-bucket",
    "tls_ca_chain": None,
    "is_s3": True,
}

# A sample TLS CA chain (list of PEM certificates) as delivered by an s3 store over the relation.
S3_TLS_CA_CHAIN = [
    "-----BEGIN CERTIFICATE-----\nZmFrZS1yb290LWNh\n-----END CERTIFICATE-----",
    "-----BEGIN CERTIFICATE-----\nZmFrZS1pbnRlcm1lZGlhdGU=\n-----END CERTIFICATE-----",
]

# The same CA chain as embedded (joined + base64-encoded) into the artifact-store Secret's data.
EXPECTED_S3_CA_BUNDLE_B64 = base64.b64encode("\n".join(S3_TLS_CA_CHAIN).encode()).decode()

BACKEND_STORE_DB_DATA = {
    "database": "mlflow",
    "host": "host",
    "username": "username",
    "password": "lorem-ipsum",
    "port": "port",
}
AUTH_DATABASE_DB_DATA = {
    "database": "mlflow_auth",
    "host": "host",
    "username": "username",
    "password": "lorem-ipsum",
    "port": "port",
}

SECRETS_TEST_FILES = ["tests/test_data/secret.yaml.j2"]

EXPECTED_SERVER_HOST = "0.0.0.0"
EXPECTED_SERVER_METRICS_PATH = "/metrics"
EXPECTED_SERVER_PORT = 5000
EXPECTED_S3_ENDPOINT = (
    f"{'https' if OBJECT_STORAGE_DATA_NORMALIZED['secure'] else 'http'}://"
    f"{OBJECT_STORAGE_DATA_NORMALIZED['host']}:{OBJECT_STORAGE_DATA_NORMALIZED['port']}"
)
EXPECTED_S3_URI = f"s3://{BUCKET_NAME}"
EXPECTED_FLASK_SECRET_KEY = "test-flask-secret-key"
EXPECTED_ADMIN_PASSWORD = "test-admin-password"
AUTH_SECRETS = {
    "flask_secret_key": EXPECTED_FLASK_SECRET_KEY,
    "admin_password": EXPECTED_ADMIN_PASSWORD,
}
EXPECTED_AUTH_ENVIRONMENT = {
    "MLFLOW_ENABLE_WORKSPACES": "true",
    "MLFLOW_RBAC_SEED_DEFAULT_ROLES": "false",
    "MLFLOW_AUTH_CONFIG_PATH": "/var/lib/pebble/default/auth/basic_auth.ini",
    "MLFLOW_FLASK_SERVER_SECRET_KEY": EXPECTED_FLASK_SECRET_KEY,
    "IDENTITY_HEADER_NAME": "kubeflow-userid",
    "PYTHONPATH": "/var/lib/pebble/default/auth",
}
EXPECTED_ENVIRONMENT_NON_PROXY_MODE = {
    "MLFLOW_BACKEND_STORE_URI": "mysql+pymysql://username:lorem-ipsum@host:port/mlflow",
    "MLFLOW_DATABASE_AUTH_URI": "mysql+pymysql://username:lorem-ipsum@host:port/mlflow_auth",
    "MLFLOW_DEFAULT_ARTIFACT_ROOT": EXPECTED_S3_URI,
    "MLFLOW_EXPOSE_PROMETHEUS": EXPECTED_SERVER_METRICS_PATH,
    "MLFLOW_HOST": EXPECTED_SERVER_HOST,
    "MLFLOW_PORT": EXPECTED_SERVER_PORT,
    "MLFLOW_SERVE_ARTIFACTS": "False",
    "MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE": "true",
    **EXPECTED_AUTH_ENVIRONMENT,
}
EXPECTED_ENVIRONMENT_PROXY_MODE = {
    "MLFLOW_BACKEND_STORE_URI": "mysql+pymysql://username:lorem-ipsum@host:port/mlflow",
    "MLFLOW_DATABASE_AUTH_URI": "mysql+pymysql://username:lorem-ipsum@host:port/mlflow_auth",
    "MLFLOW_EXPOSE_PROMETHEUS": EXPECTED_SERVER_METRICS_PATH,
    "MLFLOW_HOST": EXPECTED_SERVER_HOST,
    "MLFLOW_PORT": EXPECTED_SERVER_PORT,
    "AWS_ACCESS_KEY_ID": OBJECT_STORAGE_DATA_NORMALIZED["access_key"],
    "AWS_SECRET_ACCESS_KEY": OBJECT_STORAGE_DATA_NORMALIZED["secret_key"],
    "MLFLOW_ARTIFACTS_DESTINATION": EXPECTED_S3_URI,
    "MLFLOW_DEFAULT_ARTIFACT_ROOT": "mlflow-artifacts:/",
    "MLFLOW_S3_ENDPOINT_URL": EXPECTED_S3_ENDPOINT,
    "MLFLOW_SERVE_ARTIFACTS": "True",
    "MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE": "true",
    **EXPECTED_AUTH_ENVIRONMENT,
}


def build_expected_pebble_service_plan(environment_variables: dict) -> dict:
    """Build the expected pebble service plan for the given environment variables."""
    return {
        "mlflow-server": Service(
            "mlflow-server",
            raw={
                "summary": "Entrypoint of mlflow-server image",
                "startup": "enabled",
                "override": "replace",
                "command": "mlflow server --app-name basic-auth",
                "environment": environment_variables,
            },
        )
    }


EXPECTED_INGRESS_PATH_MATCHED_PREFIX = "/mlflow/"
EXPECTED_INGRESS_PATH_REWRITTEN_PREFIX = "/"
EXPECTED_K8S_SERVICE_HTTP_PORT = EXPECTED_SERVER_PORT
RELATION_ENDPOINT_FOR_INGRESS_IN_AMBIENT_MODE = "istio-ingress-route"
RELATION_ENDPOINT_FOR_INGRESS_IN_SIDECAR_MODE = "ingress"
RELATION_ENDPOINT_FOR_SERVICE_MESH = "service-mesh"
RELATION_ENDPOINT_FOR_SECRETS = "secrets"
RELATION_ENDPOINT_FOR_PODDEFAULTS = "pod-defaults"
RELATION_ENDPOINT_FOR_BACKEND_STORE_DB = "backend-store-db"
RELATION_ENDPOINT_FOR_AUTH_DATABASE_DB = "auth-db"

INGRESS_DATA = {
    "prefix": EXPECTED_INGRESS_PATH_MATCHED_PREFIX,
    "rewrite": EXPECTED_INGRESS_PATH_REWRITTEN_PREFIX,
    "service": DEFAULT_JUJU_APP_NAME,
    "namespace": MODEL_NAME,
    "port": EXPECTED_K8S_SERVICE_HTTP_PORT,
}

# Rendered manifests expected from the SECRETS_FILES and PODDEFAULTS_FILES templates:
EXPECTED_MLFLOW_ENDPOINT = (
    f"http://{CHARM_NAME}.{MODEL_NAME}.svc.cluster.local:{EXPECTED_K8S_SERVICE_HTTP_PORT}"
)
EXPECTED_SECRET_MANIFEST = {
    "apiVersion": "v1",
    "kind": "Secret",
    "metadata": {"name": f"{CHARM_NAME}-minio-artifact"},
    "stringData": {"AWS_ACCESS_KEY_ID": "a", "AWS_SECRET_ACCESS_KEY": "s"},
}
# Secret variant for a TLS artifact store: the CA bundle is embedded (base64) under `data`.
EXPECTED_SECRET_MANIFEST_WITH_CA = {
    **EXPECTED_SECRET_MANIFEST,
    "data": {"ca-bundle.pem": EXPECTED_S3_CA_BUNDLE_B64},
}
EXPECTED_MINIO_PODDEFAULT_MANIFEST = {
    "apiVersion": "kubeflow.org/v1alpha1",
    "kind": "PodDefault",
    "metadata": {"name": f"{CHARM_NAME}-access-minio"},
    "spec": {
        "desc": "Allow access to Minio",
        "selector": {"matchLabels": {"access-minio": "true"}},
        "env": [
            {
                "name": "AWS_ACCESS_KEY_ID",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": f"{CHARM_NAME}-minio-artifact",
                        "key": "AWS_ACCESS_KEY_ID",
                        "optional": False,
                    }
                },
            },
            {
                "name": "AWS_SECRET_ACCESS_KEY",
                "valueFrom": {
                    "secretKeyRef": {
                        "name": f"{CHARM_NAME}-minio-artifact",
                        "key": "AWS_SECRET_ACCESS_KEY",
                        "optional": False,
                    }
                },
            },
            {"name": "MINIO_ENDPOINT_URL", "value": EXPECTED_S3_ENDPOINT},
        ],
    },
}
# access-minio PodDefault variant for a TLS artifact store: the CA bundle Secret key is mounted
# read-only into client pods and AWS_CA_BUNDLE points boto3 at it.
EXPECTED_MINIO_PODDEFAULT_MANIFEST_WITH_CA = {
    **EXPECTED_MINIO_PODDEFAULT_MANIFEST,
    "spec": {
        **EXPECTED_MINIO_PODDEFAULT_MANIFEST["spec"],
        "env": [
            *EXPECTED_MINIO_PODDEFAULT_MANIFEST["spec"]["env"],
            {"name": "AWS_CA_BUNDLE", "value": "/etc/mlflow/certs/ca-bundle.pem"},
        ],
        "volumeMounts": [
            {"name": "s3-ca-bundle", "mountPath": "/etc/mlflow/certs", "readOnly": True},
        ],
        "volumes": [
            {
                "name": "s3-ca-bundle",
                "secret": {
                    "secretName": f"{CHARM_NAME}-minio-artifact",
                    "items": [{"key": "ca-bundle.pem", "path": "ca-bundle.pem"}],
                },
            },
        ],
    },
}
EXPECTED_MLFLOW_PODDEFAULT_MANIFEST_NON_PROXY_MODE = {
    "apiVersion": "kubeflow.org/v1alpha1",
    "kind": "PodDefault",
    "metadata": {"name": f"{CHARM_NAME}-minio"},
    "spec": {
        "desc": "Allow access to MLFlow",
        "env": [
            {"name": "MLFLOW_S3_ENDPOINT_URL", "value": EXPECTED_S3_ENDPOINT},
            {"name": "MLFLOW_TRACKING_URI", "value": EXPECTED_MLFLOW_ENDPOINT},
        ],
        "selector": {"matchLabels": {"mlflow-server-minio": "true"}},
    },
}
EXPECTED_MLFLOW_PODDEFAULT_MANIFEST_PROXY_MODE = {
    "apiVersion": "kubeflow.org/v1alpha1",
    "kind": "PodDefault",
    "metadata": {"name": f"{CHARM_NAME}-minio"},
    "spec": {
        "desc": "Allow access to MLFlow",
        "env": [
            {"name": "MLFLOW_ENABLE_PROXY_MULTIPART_DOWNLOAD", "value": "false"},
            {"name": "MLFLOW_ENABLE_PROXY_MULTIPART_UPLOAD", "value": "false"},
            {"name": "MLFLOW_TRACKING_URI", "value": EXPECTED_MLFLOW_ENDPOINT},
        ],
        "selector": {"matchLabels": {"mlflow-server-minio": "true"}},
    },
}


def build_secrets_context(is_proxy_mode_enabled: bool, s3_ca_bundle_b64: str = "") -> dict:
    """Build a rendering context with only the keys the secrets templates require."""
    return {
        "app_name": CHARM_NAME,
        "access_key": "a",
        "secret_access_key": "s",
        "is_proxy_mode_enabled": is_proxy_mode_enabled,
        "s3_ca_bundle_b64": s3_ca_bundle_b64,
    }


def build_poddefaults_context(
    is_proxy_mode_enabled: bool, s3_ca_bundle_present: bool = False
) -> dict:
    """Build a rendering context with only the keys the poddefaults templates require."""
    return {
        "app_name": CHARM_NAME,
        "s3_endpoint": EXPECTED_S3_ENDPOINT,
        "mlflow_endpoint": EXPECTED_MLFLOW_ENDPOINT,
        "is_proxy_mode_enabled": is_proxy_mode_enabled,
        "s3_ca_bundle_present": s3_ca_bundle_present,
    }


@pytest.fixture(scope="function")
def harness() -> Harness:
    """Create and return Harness for testing."""

    harness = Harness(MlflowCharm)

    harness.set_model_name(MODEL_NAME)

    harness.set_leader(True)

    harness.set_can_connect("mlflow-server", True)
    harness.set_can_connect("mlflow-prometheus-exporter", True)

    # registering a default no-op handler for any `python3 ...` command executed in the
    # mlflow-server workload container:
    harness.handle_exec("mlflow-server", ["python3"], result=0)
    # NOTE: several reconcile paths shell out some Python script execution on the workload via
    # `container.exec(["python3", "-c", <snippet>, backend_store_uri])`, such as
    # `_ensure_trigger_creation_allowed` and `_is_database_schema_out_of_date`, and without this
    # registered handler, the Harness would raise on the first such exec, while this makes those
    # paths succeed by default (exit code 0, empty stdout/stderr) - and then tests that need to
    # assert on the exec call, or to simulate a failure, replace `harness.charm.container.exec`
    # with their own MagicMock, which takes precedence over this handler

    return harness


def add_relation(harness: Harness, relation_endpoint: str) -> tuple[int, str]:
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
    def test_get_artifact_store_data_failure_missing_storage_object(
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
    def test_get_artifact_store_data_failure_bad_storage_object(
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
    def test_get_artifact_store_data_success(self, harness: Harness):
        harness = add_object_storage_to_harness(harness)
        harness.begin_with_initial_hooks()
        assert harness.charm.model.unit.status == BlockedStatus(
            "Please add relation to the database"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_backend_store_db_data_success(self, harness: Harness):
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
        res = harness.charm._get_backend_store_db_data()
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
    def test_get_auth_database_db_data_success(self, harness: Harness):
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
        res = harness.charm._get_auth_database_db_data()
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
    def test_get_backend_store_db_data_failure_wrong_data(self, harness: Harness):
        """Test with missing username and password in databag"""
        database = MagicMock()
        fetch_relation_data = MagicMock()
        fetch_relation_data.return_value = {"test-db-data": {"endpoints": "host:port"}}
        database.fetch_relation_data = fetch_relation_data
        harness.model.get_relation = MagicMock()
        harness.begin()
        harness.charm.database = database
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_backend_store_db_data()
        assert e_info.value.status_type(WaitingStatus)
        assert (
            f"Incorrect data found in relation {RELATION_ENDPOINT_FOR_BACKEND_STORE_DB}"
            in str(e_info)
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_auth_database_db_data_data_failure_wrong_data(self, harness: Harness):
        """Test with missing username and password in databag"""
        database = MagicMock()
        fetch_relation_data = MagicMock()
        fetch_relation_data.return_value = {"test-db-data": {"endpoints": "host:port"}}
        database.fetch_relation_data = fetch_relation_data
        harness.model.get_relation = MagicMock()
        harness.begin()
        harness.charm.database = database
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_auth_database_db_data()
        assert e_info.value.status_type(WaitingStatus)
        assert (
            f"Incorrect data found in relation {RELATION_ENDPOINT_FOR_AUTH_DATABASE_DB}"
            in str(e_info)
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_backend_store_db_data_failure_waiting(self, harness: Harness):
        database = MagicMock()
        fetch_relation_data = MagicMock()
        fetch_relation_data.return_value = {}
        database.fetch_relation_data = fetch_relation_data
        harness.begin()
        harness.charm.database = database
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_backend_store_db_data()

        assert e_info.value.status_type(BlockedStatus)
        assert f"Please add the relation {RELATION_ENDPOINT_FOR_BACKEND_STORE_DB}" in str(e_info)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_get_auth_database_db_data_failure_waiting(self, harness: Harness):
        database = MagicMock()
        fetch_relation_data = MagicMock()
        fetch_relation_data.return_value = {}
        database.fetch_relation_data = fetch_relation_data
        harness.begin()
        harness.charm.database = database
        with pytest.raises(ErrorWithStatus) as e_info:
            harness.charm._get_auth_database_db_data()

        assert e_info.value.status_type(BlockedStatus)
        assert f"Please add the relation {RELATION_ENDPOINT_FOR_AUTH_DATABASE_DB}" in str(e_info)

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
        "charm.MlflowCharm._get_artifact_store_data",
        return_value=OBJECT_STORAGE_DATA_NORMALIZED,
    )
    def test_ensure_bucket_exists_when_bucket_present(
        self, _get_artifact_store_data: MagicMock, s3_wrapper_cls: MagicMock, harness: Harness
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
        "charm.MlflowCharm._get_artifact_store_data",
        return_value=OBJECT_STORAGE_DATA_NORMALIZED,
    )
    def test_ensure_bucket_exists_creates_missing_bucket(
        self, _get_artifact_store_data: MagicMock, s3_wrapper_cls: MagicMock, harness: Harness
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
        "charm.MlflowCharm._get_artifact_store_data",
        return_value=OBJECT_STORAGE_DATA_NORMALIZED,
    )
    def test_ensure_bucket_exists_connection_error_waiting(
        self, _get_artifact_store_data: MagicMock, s3_wrapper_cls: MagicMock, harness: Harness
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
        "charm.MlflowCharm._get_artifact_store_data",
        return_value={**OBJECT_STORAGE_DATA_NORMALIZED, "bucket": ""},
    )
    def test_on_event_missing_bucket_sets_blocked_status(
        self, _get_artifact_store_data: MagicMock, s3_wrapper_cls: MagicMock, harness: Harness
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
        "charm.MlflowCharm._get_artifact_store_data",
        return_value=OBJECT_STORAGE_DATA_NORMALIZED,
    )
    def test_on_event_bucket_connection_error_sets_waiting_status(
        self, _get_artifact_store_data: MagicMock, s3_wrapper_cls: MagicMock, harness: Harness
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
    )
    @pytest.mark.parametrize(
        "expected_environment",
        [EXPECTED_ENVIRONMENT_PROXY_MODE, EXPECTED_ENVIRONMENT_NON_PROXY_MODE],
        ids=["proxy-mode", "no-proxy-mode"],
    )
    def test_update_layer_success(
        self,
        mock_service_environment: PropertyMock,
        harness: Harness,
        expected_environment,
    ):
        mock_service_environment.return_value = expected_environment
        harness.begin()
        update_layer(
            harness.charm._container_name,
            harness.charm.container,
            harness.charm._mlflow_server_layer,
            harness.charm.logger,
        )
        assert harness.charm.container.get_plan().services == build_expected_pebble_service_plan(
            expected_environment
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.MlflowCharm._get_interfaces", lambda *args, **kw: None)
    @patch(
        "charm.MlflowCharm._get_backend_store_db_data", lambda *args, **kw: BACKEND_STORE_DB_DATA
    )
    @patch(
        "charm.MlflowCharm._get_auth_database_db_data", lambda *args, **kw: AUTH_DATABASE_DB_DATA
    )
    @patch("charm.MlflowCharm._get_or_create_auth_secrets", lambda *args, **kw: AUTH_SECRETS)
    @patch("charm.MlflowCharm._get_artifact_store_data")
    @pytest.mark.parametrize(
        "serve_artifacts, tls_ca_chain, region, expected_environment",
        [
            # config option to serve artifacts not explicitly set -> default value -> no proxy mode:
            (None, None, "", EXPECTED_ENVIRONMENT_NON_PROXY_MODE),
            # config option to serve artifacts explicitly set to False -> no proxy mode:
            (False, None, "", EXPECTED_ENVIRONMENT_NON_PROXY_MODE),
            # config option to serve artifacts explicitly set to True -> proxy mode:
            (True, None, "", EXPECTED_ENVIRONMENT_PROXY_MODE),
            # proxy mode against a TLS store -> CA exposed to the server via AWS_CA_BUNDLE:
            (
                True,
                S3_TLS_CA_CHAIN,
                "",
                {**EXPECTED_ENVIRONMENT_PROXY_MODE, "AWS_CA_BUNDLE": S3_CA_BUNDLE_CONTAINER_PATH},
            ),
            # proxy mode against a store advertising a region -> region exposed to the server via
            # AWS_DEFAULT_REGION:
            (
                True,
                None,
                "us-east-1",
                {**EXPECTED_ENVIRONMENT_PROXY_MODE, "AWS_DEFAULT_REGION": "us-east-1"},
            ),
        ],
        ids=[
            "default-no-proxy-mode",
            "no-proxy-mode",
            "proxy-mode",
            "proxy-mode-with-tls",
            "proxy-mode-with-region",
        ],
    )
    def test_generate_environment(
        self,
        mock_get_artifact_store_data,
        harness: Harness,
        serve_artifacts,
        tls_ca_chain,
        region,
        expected_environment,
    ):
        mock_get_artifact_store_data.return_value = {
            **OBJECT_STORAGE_DATA_NORMALIZED,
            "bucket": "",
            "tls_ca_chain": tls_ca_chain,
            "region": region,
        }
        if serve_artifacts is not None:
            harness.update_config({CONFIG_OPTION_NAME_FOR_SERVE_ARTIFACTS: serve_artifacts})
        harness.begin()
        envs = harness.charm._generate_environment()
        assert envs == expected_environment

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.MlflowCharm._get_interfaces", lambda *args, **kw: None)
    @patch(
        "charm.MlflowCharm._get_backend_store_db_data", lambda *args, **kw: BACKEND_STORE_DB_DATA
    )
    @patch(
        "charm.MlflowCharm._get_auth_database_db_data", lambda *args, **kw: AUTH_DATABASE_DB_DATA
    )
    @patch("charm.MlflowCharm._get_or_create_auth_secrets", lambda *args, **kw: AUTH_SECRETS)
    @patch("charm.MlflowCharm._get_artifact_store_data")
    @pytest.mark.parametrize(
        "serve_artifacts, tls_ca_chain, expected_environment",
        [
            (True, None, EXPECTED_ENVIRONMENT_PROXY_MODE),
            (False, S3_TLS_CA_CHAIN, EXPECTED_ENVIRONMENT_NON_PROXY_MODE),
        ],
        ids=["proxy-mode-without-tls", "tls-without-proxy-mode"],
    )
    def test_generate_environment_omits_ca_bundle_when_not_needed(
        self,
        mock_get_artifact_store_data,
        harness: Harness,
        serve_artifacts,
        tls_ca_chain,
        expected_environment,
    ):
        """AWS_CA_BUNDLE is only exposed for a TLS artifact store in proxy mode."""
        mock_get_artifact_store_data.return_value = {
            **OBJECT_STORAGE_DATA_NORMALIZED,
            "bucket": "",
            "tls_ca_chain": tls_ca_chain,
        }
        harness.update_config({CONFIG_OPTION_NAME_FOR_SERVE_ARTIFACTS: serve_artifacts})
        harness.begin()
        envs = harness.charm._generate_environment()
        assert "AWS_CA_BUNDLE" not in envs
        assert envs == expected_environment

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_reconcile_s3_ca_bundle_pushes_bundle_in_proxy_mode_with_tls(self, harness: Harness):
        """A TLS store in proxy mode has its CA chain pushed to the workload container."""
        harness.update_config({CONFIG_OPTION_NAME_FOR_SERVE_ARTIFACTS: True})
        harness.begin()
        harness.charm._container.push = MagicMock()

        harness.charm._reconcile_s3_ca_bundle(
            {**OBJECT_STORAGE_DATA_NORMALIZED, "tls_ca_chain": S3_TLS_CA_CHAIN}
        )

        harness.charm._container.push.assert_called_once_with(
            S3_CA_BUNDLE_CONTAINER_PATH, "\n".join(S3_TLS_CA_CHAIN), make_dirs=True
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @pytest.mark.parametrize(
        "serve_artifacts, tls_ca_chain",
        [
            (True, None),
            (False, S3_TLS_CA_CHAIN),
            (False, None),
        ],
        ids=["proxy-mode-without-tls", "tls-without-proxy-mode", "neither"],
    )
    def test_reconcile_s3_ca_bundle_skips_push_when_not_needed(
        self, harness: Harness, serve_artifacts, tls_ca_chain
    ):
        """The CA bundle is only pushed in proxy mode against a TLS store."""
        harness.update_config({CONFIG_OPTION_NAME_FOR_SERVE_ARTIFACTS: serve_artifacts})
        harness.begin()
        harness.charm._container.push = MagicMock()

        harness.charm._reconcile_s3_ca_bundle(
            {**OBJECT_STORAGE_DATA_NORMALIZED, "tls_ca_chain": tls_ca_chain}
        )

        harness.charm._container.push.assert_not_called()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.S3BucketWrapper")
    @patch(
        "charm.MlflowCharm._get_artifact_store_data",
        return_value={**OBJECT_STORAGE_DATA_NORMALIZED, "tls_ca_chain": S3_TLS_CA_CHAIN},
    )
    def test_on_event_skips_ca_bundle_push_when_container_not_ready(
        self, _get_artifact_store_data: MagicMock, s3_wrapper_cls: MagicMock, harness: Harness
    ):
        """_on_event stops before pushing the CA bundle when the workload container is down."""
        harness.set_can_connect("mlflow-server", False)
        harness.update_config({CONFIG_OPTION_NAME_FOR_SERVE_ARTIFACTS: True})
        harness.begin()
        harness.charm._container.push = MagicMock()

        harness.charm._on_event(None)

        harness.charm._container.push.assert_not_called()
        assert not isinstance(harness.charm.model.unit.status, ActiveStatus)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @pytest.mark.parametrize("serve_artifacts", [True, False], ids=["proxy-mode", "no-proxy-mode"])
    def test_proxy_mode(self, harness: Harness, serve_artifacts):
        """Test that the proxy_mode property mirrors the serve_artifacts config option."""
        harness.update_config({CONFIG_OPTION_NAME_FOR_SERVE_ARTIFACTS: serve_artifacts})
        harness.begin()
        assert harness.charm.proxy_mode is serve_artifacts

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @pytest.mark.parametrize(
        "secure, expected_scheme",
        [(False, "http"), (True, "https")],
        ids=["insecure", "secure"],
    )
    def test_extract_s3_endpoint(self, harness: Harness, secure, expected_scheme):
        """Test that the S3 endpoint URL is correctly extracted from artifact store data."""
        harness.begin()
        artifact_store_data = {**OBJECT_STORAGE_DATA_NORMALIZED, "secure": secure}
        endpoint = harness.charm._extract_s3_endpoint(artifact_store_data)
        assert endpoint == (
            f"{expected_scheme}://"
            f"{OBJECT_STORAGE_DATA_NORMALIZED['host']}:{OBJECT_STORAGE_DATA_NORMALIZED['port']}"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.MlflowCharm._get_interfaces", lambda *args, **kw: None)
    @patch("charm.MlflowCharm._get_artifact_store_data")
    @pytest.mark.parametrize("serve_artifacts", [True, False], ids=["proxy-mode", "no-proxy-mode"])
    @pytest.mark.parametrize(
        "tls_ca_chain, expected_ca_bundle_b64",
        [(None, ""), (S3_TLS_CA_CHAIN, EXPECTED_S3_CA_BUNDLE_B64)],
        ids=["without-tls", "with-tls"],
    )
    def test_secrets_context(
        self,
        mock_get_artifact_store_data: MagicMock,
        harness: Harness,
        serve_artifacts,
        tls_ca_chain,
        expected_ca_bundle_b64,
    ):
        """Test that the context for secrets carries the expected data."""
        mock_get_artifact_store_data.return_value = {
            **OBJECT_STORAGE_DATA_NORMALIZED,
            "tls_ca_chain": tls_ca_chain,
        }
        harness.update_config({CONFIG_OPTION_NAME_FOR_SERVE_ARTIFACTS: serve_artifacts})
        harness.begin()
        context = harness.charm.secrets_context
        assert context["app_name"] == DEFAULT_JUJU_APP_NAME
        assert context["access_key"] == OBJECT_STORAGE_DATA_NORMALIZED["access_key"]
        assert context["secret_access_key"] == OBJECT_STORAGE_DATA_NORMALIZED["secret_key"]
        assert context["is_proxy_mode_enabled"] is serve_artifacts
        assert context["s3_ca_bundle_b64"] == expected_ca_bundle_b64

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch("charm.MlflowCharm._get_interfaces", lambda *args, **kw: None)
    @patch("charm.MlflowCharm._get_artifact_store_data")
    @pytest.mark.parametrize("serve_artifacts", [True, False], ids=["proxy-mode", "no-proxy-mode"])
    @pytest.mark.parametrize(
        "tls_ca_chain, expected_ca_bundle_present",
        [(None, False), (S3_TLS_CA_CHAIN, True)],
        ids=["without-tls", "with-tls"],
    )
    def test_poddefaults_context(
        self,
        mock_get_artifact_store_data: MagicMock,
        harness: Harness,
        serve_artifacts,
        tls_ca_chain,
        expected_ca_bundle_present,
    ):
        """Test that the context for poddefaults carries the expected data."""
        mock_get_artifact_store_data.return_value = {
            **OBJECT_STORAGE_DATA_NORMALIZED,
            "tls_ca_chain": tls_ca_chain,
        }
        harness.update_config({CONFIG_OPTION_NAME_FOR_SERVE_ARTIFACTS: serve_artifacts})
        harness.begin()
        context = harness.charm.poddefaults_context
        assert context["app_name"] == DEFAULT_JUJU_APP_NAME
        assert context["s3_endpoint"] == EXPECTED_S3_ENDPOINT
        assert context["mlflow_endpoint"] == (
            f"http://{CHARM_NAME}.{MODEL_NAME}.svc.cluster.local:{EXPECTED_K8S_SERVICE_HTTP_PORT}"
        )
        assert context["is_proxy_mode_enabled"] is serve_artifacts
        assert context["s3_ca_bundle_present"] is expected_ca_bundle_present

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
    @pytest.mark.parametrize(
        "manifest_files, context, expected_manifests",
        [
            (
                SECRETS_FILES,
                build_secrets_context(is_proxy_mode_enabled=False),
                [EXPECTED_SECRET_MANIFEST],
            ),
            (
                SECRETS_FILES,
                build_secrets_context(
                    is_proxy_mode_enabled=False, s3_ca_bundle_b64=EXPECTED_S3_CA_BUNDLE_B64
                ),
                [EXPECTED_SECRET_MANIFEST_WITH_CA],
            ),
            (SECRETS_FILES, build_secrets_context(is_proxy_mode_enabled=True), []),
            (
                PODDEFAULTS_FILES,
                build_poddefaults_context(is_proxy_mode_enabled=False),
                [
                    EXPECTED_MINIO_PODDEFAULT_MANIFEST,
                    EXPECTED_MLFLOW_PODDEFAULT_MANIFEST_NON_PROXY_MODE,
                ],
            ),
            (
                PODDEFAULTS_FILES,
                build_poddefaults_context(is_proxy_mode_enabled=False, s3_ca_bundle_present=True),
                [
                    EXPECTED_MINIO_PODDEFAULT_MANIFEST_WITH_CA,
                    EXPECTED_MLFLOW_PODDEFAULT_MANIFEST_NON_PROXY_MODE,
                ],
            ),
            (
                PODDEFAULTS_FILES,
                build_poddefaults_context(is_proxy_mode_enabled=True),
                [EXPECTED_MLFLOW_PODDEFAULT_MANIFEST_PROXY_MODE],
            ),
        ],
        ids=[
            "secrets-proxy-disabled-renders-secret",
            "secrets-proxy-disabled-with-tls-embeds-ca-bundle",
            "secrets-proxy-enabled-skips-empty-document",
            "poddefaults-proxy-disabled-renders-both",
            "poddefaults-proxy-disabled-with-tls-mounts-ca-bundle",
            "poddefaults-proxy-enabled-skips-minio-poddefault",
        ],
    )
    def test_create_manifests_skips_empty_documents(
        self, harness: Harness, manifest_files, context, expected_manifests
    ):
        """Test that templates rendering to empty documents are skipped."""
        harness.begin()
        manifests_items = harness.charm._create_manifests(manifest_files, context)
        assert [item.manifest for item in manifests_items] == expected_manifests

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
    @pytest.mark.parametrize(
        "relation_endpoint, wrapper_attribute, manifest_files, context, expected_manifests",
        [
            (
                RELATION_ENDPOINT_FOR_SECRETS,
                "secrets_manifests_wrapper",
                SECRETS_FILES,
                build_secrets_context(is_proxy_mode_enabled=False),
                [EXPECTED_SECRET_MANIFEST],
            ),
            (
                RELATION_ENDPOINT_FOR_SECRETS,
                "secrets_manifests_wrapper",
                SECRETS_FILES,
                build_secrets_context(is_proxy_mode_enabled=True),
                [],
            ),
            (
                RELATION_ENDPOINT_FOR_PODDEFAULTS,
                "poddefaults_manifests_wrapper",
                PODDEFAULTS_FILES,
                build_poddefaults_context(is_proxy_mode_enabled=False),
                [
                    EXPECTED_MINIO_PODDEFAULT_MANIFEST,
                    EXPECTED_MLFLOW_PODDEFAULT_MANIFEST_NON_PROXY_MODE,
                ],
            ),
            (
                RELATION_ENDPOINT_FOR_PODDEFAULTS,
                "poddefaults_manifests_wrapper",
                PODDEFAULTS_FILES,
                build_poddefaults_context(is_proxy_mode_enabled=True),
                [EXPECTED_MLFLOW_PODDEFAULT_MANIFEST_PROXY_MODE],
            ),
        ],
        ids=[
            "secrets-proxy-disabled-applies-secret",
            "secrets-proxy-enabled-applies-nothing",
            "poddefaults-proxy-disabled-applies-both",
            "poddefaults-proxy-enabled-applies-mlflow-poddefault-only",
        ],
    )
    def test_send_manifests_applies_rendered_manifests_to_relation(
        self,
        harness: Harness,
        relation_endpoint,
        wrapper_attribute,
        manifest_files,
        context,
        expected_manifests,
    ):
        """Test that the rendered manifests are written verbatim to the relation databag."""
        relation_id, _ = add_relation(harness, relation_endpoint=relation_endpoint)
        harness.begin()

        harness.charm._send_manifests(
            context, manifest_files, getattr(harness.charm, wrapper_attribute)
        )

        application_databag = harness.get_relation_data(relation_id, harness.charm.app.name)
        applied_manifests = json.loads(application_databag[KUBERNETES_MANIFESTS_FIELD])
        assert applied_manifests == expected_manifests

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    @patch(
        "charm.MlflowCharm._ensure_bucket_exists",
        lambda *args, **kw: None,
    )
    @patch(
        "charm.MlflowCharm._get_artifact_store_data", return_value=OBJECT_STORAGE_DATA_NORMALIZED
    )
    @patch("charm.MlflowCharm._get_backend_store_db_data", return_value=BACKEND_STORE_DB_DATA)
    @patch("charm.MlflowCharm._get_auth_database_db_data", return_value=AUTH_DATABASE_DB_DATA)
    def test_on_event_waiting_for_exporter(
        self,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
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
    @patch(
        "charm.MlflowCharm._get_artifact_store_data", return_value=OBJECT_STORAGE_DATA_NORMALIZED
    )
    @patch("charm.MlflowCharm._get_backend_store_db_data", return_value=BACKEND_STORE_DB_DATA)
    @patch("charm.MlflowCharm._get_auth_database_db_data", return_value=AUTH_DATABASE_DB_DATA)
    def test_on_event(
        self,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
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
    def test_run_database_migration_success(self, harness: Harness):
        harness.begin()
        backend_store_uri = "mysql+pymysql://username:password@host:port/mlflow"
        auth_database_uri = "mysql+pymysql://username:password@host:port/mlflow_auth"
        process = MagicMock()
        process.wait_output.return_value = ("migration output", "")
        exec_mock = MagicMock(return_value=process)
        harness.charm.container.exec = exec_mock
        harness.charm._run_database_migration(backend_store_uri)
        harness.charm._run_database_migration(auth_database_uri)
        exec_mock.assert_any_call(["mlflow", "db", "upgrade", backend_store_uri])
        exec_mock.assert_any_call(["mlflow", "db", "upgrade", auth_database_uri])
        # the command output is consumed so failures surface as an ExecError:
        process.wait_output.assert_called()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_run_database_migration_failure(self, harness: Harness, caplog):
        harness.begin()
        process = MagicMock()
        process.wait_output.side_effect = ExecError(
            command=["mlflow", "db", "upgrade"], exit_code=1, stdout="", stderr="boom"
        )
        harness.charm.container.exec = MagicMock(return_value=process)
        with caplog.at_level(logging.ERROR):
            with pytest.raises(ErrorWithStatus) as e_info:
                harness.charm._run_database_migration("mysql+pymysql://u:p@h:3306/mlflow")
        assert e_info.value.status.__class__ is BlockedStatus
        assert "Database schema migration failed" in str(e_info)
        # the raw stderr (which can contain the backend store URI/credentials) is not leaked into
        # the user-facing status message:
        assert "boom" not in str(e_info.value.status.message)
        # ...nor into the logs (only the exit code and static guidance are logged):
        assert "boom" not in caplog.text
        assert "exit code 1" in caplog.text

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_is_database_schema_out_of_date_true_marker_among_other_output(self, harness: Harness):
        """The marker is detected even when surrounded by other stdout (e.g. warnings).

        Also verifies the probe is read-only: it never invokes `mlflow db upgrade`.
        """
        harness.begin()
        backend_store_uri = "mysql+pymysql://u:p@h:3306/mlflow"
        process = MagicMock()
        process.wait_output.return_value = (
            f"some warning line\n{SCHEMA_OUT_OF_DATE_MARKER}\ntrailing\n",
            "",
        )
        exec_mock = MagicMock(return_value=process)
        harness.charm.container.exec = exec_mock
        assert harness.charm._is_database_schema_out_of_date(backend_store_uri)
        # the probe is read-only: it runs a python3 verification snippet and never shells out to
        # `mlflow db upgrade` (assert intent rather than the exact argv, to avoid coupling to the
        # invocation shape):
        assert exec_mock.call_count == 1
        command = exec_mock.call_args.args[0]
        assert command[0] == "python3"
        assert "mlflow" not in command

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_is_database_schema_out_of_date_false_when_up_to_date(self, harness: Harness):
        harness.begin()
        process = MagicMock()
        process.wait_output.return_value = ("", "")
        harness.charm.container.exec = MagicMock(return_value=process)
        assert not harness.charm._is_database_schema_out_of_date(
            "mysql+pymysql://u:p@h:3306/mlflow"
        )

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_is_database_schema_out_of_date_raises_waiting_on_exec_error(self, harness: Harness):
        """An undeterminable schema state raises Waiting so the caller can defer and retry."""
        harness.begin()
        process = MagicMock()
        process.wait_output.side_effect = ExecError(
            command=["python3"], exit_code=1, stdout="", stderr="unrelated failure"
        )
        harness.charm.container.exec = MagicMock(return_value=process)
        with pytest.raises(ErrorWithStatus) as exc_info:
            harness.charm._is_database_schema_out_of_date("mysql+pymysql://u:p@h:3306/mlflow")
        assert exc_info.value.status_type is WaitingStatus

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    # TODO: remove once this issue is fixed: https://github.com/mlflow/mlflow/issues/19943
    def test_ensure_trigger_creation_allowed_persists_variable(self, harness: Harness):
        harness.begin()
        backend_store_uri = "mysql+pymysql://u:p@h:3306/mlflow"
        process = MagicMock()
        process.wait_output.return_value = ("", "")
        exec_mock = MagicMock(return_value=process)
        harness.charm.container.exec = exec_mock
        harness.charm._ensure_trigger_creation_allowed(backend_store_uri)
        exec_mock.assert_called_once_with(
            ["python3", "-c", ENABLE_TRIGGER_CREATION_SNIPPET, backend_store_uri]
        )
        process.wait_output.assert_called_once_with()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    # TODO: remove once this issue is fixed: https://github.com/mlflow/mlflow/issues/19943
    def test_ensure_trigger_creation_allowed_raises_waiting_on_exec_error(self, harness: Harness):
        """An unreachable database raises Waiting so the caller can defer and retry."""
        harness.begin()
        process = MagicMock()
        process.wait_output.side_effect = ExecError(
            command=["python3"], exit_code=1, stdout="", stderr="cannot connect"
        )
        harness.charm.container.exec = MagicMock(return_value=process)
        with pytest.raises(ErrorWithStatus) as exc_info:
            harness.charm._ensure_trigger_creation_allowed("mysql+pymysql://u:p@h:3306/mlflow")
        assert exc_info.value.status_type is WaitingStatus
        # the raw stderr (which can contain the backend store URI/credentials) is not leaked:
        assert "cannot connect" not in str(exc_info.value.status.message)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    # TODO: remove once this issue is fixed: https://github.com/mlflow/mlflow/issues/19943
    def test_ensure_trigger_creation_allowed_raises_blocked_on_privilege_error(
        self, harness: Harness
    ):
        """A missing SYSTEM_VARIABLES_ADMIN privilege (error 1227) raises Blocked, not Waiting.

        Retrying cannot succeed when the relation user was created without the `charmed_dba` role
        (e.g. an in-place upgrade), so the charm must surface an actionable Blocked status instead
        of looping in Waiting.
        """
        harness.begin()
        process = MagicMock()
        process.wait_output.side_effect = ExecError(
            command=["python3"],
            exit_code=1,
            stdout="",
            stderr=(
                "pymysql.err.OperationalError: (1227, 'Access denied; you need (at least one "
                "of) the SUPER or SYSTEM_VARIABLES_ADMIN privilege(s) for this operation')"
            ),
        )
        harness.charm.container.exec = MagicMock(return_value=process)
        with pytest.raises(ErrorWithStatus) as exc_info:
            harness.charm._ensure_trigger_creation_allowed("mysql+pymysql://u:p@h:3306/mlflow")
        assert exc_info.value.status_type is BlockedStatus
        # the short status message points the operator to the logs for remediation details:
        assert (
            str(exc_info.value.status.message)
            == "Database user lacks privileges to migrate the schema. Check the unit logs "
            "and act accordingly."
        )
        # the raw stderr (which can contain the backend store URI/credentials) is not leaked:
        assert "Access denied" not in str(exc_info.value.status.message)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_reconcile_database_schema_migrates_when_out_of_date(self, harness: Harness):
        harness.begin()
        backend_store_uri = "mysql+pymysql://u:p@h:3306/mlflow"
        harness.charm._get_backend_store_uri = MagicMock(return_value=backend_store_uri)
        harness.charm._ensure_trigger_creation_allowed = MagicMock()
        harness.charm._is_database_schema_out_of_date = MagicMock(return_value=True)

        # capture the unit status observed at the moment the migration is invoked, to assert the
        # Maintenance status is set *before* the (potentially long-running) migration starts:
        observed_status = {}

        def _record_status(uri):
            observed_status["value"] = harness.charm.unit.status

        harness.charm._run_database_migration = MagicMock(side_effect=_record_status)
        harness.charm._reconcile_database_schema()

        # the migration trigger is permitted before the migration runs:
        harness.charm._ensure_trigger_creation_allowed.assert_called_once_with(backend_store_uri)
        harness.charm._is_database_schema_out_of_date.assert_called_once_with(backend_store_uri)
        harness.charm._run_database_migration.assert_called_once_with(backend_store_uri)
        assert isinstance(observed_status["value"], MaintenanceStatus)

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_on_upgrade_charm_migrates_schema(self, harness: Harness):
        """On the upgrade-charm (refresh) event the migration runs.

        The workload replan and status reconcile are left to the `config-changed` handler that
        Juju always fires right after `upgrade-charm`, so this handler must NOT call `_on_event`
        itself; it only migrates the schema.
        """
        harness.set_leader(True)
        harness.begin()
        harness.charm._reconcile_database_schema = MagicMock()
        harness.charm._on_event = MagicMock()
        event = MagicMock()

        with patch.object(harness.charm.container, "can_connect", return_value=True):
            harness.charm._on_upgrade_charm(event)

        harness.charm._reconcile_database_schema.assert_called_once()
        harness.charm._on_event.assert_not_called()
        event.defer.assert_not_called()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_on_upgrade_charm_skips_migration_when_not_leader(self, harness: Harness):
        """A non-leader unit does not run the migration and does not defer."""
        harness.set_leader(False)
        harness.begin()
        harness.charm._reconcile_database_schema = MagicMock()
        event = MagicMock()

        with patch.object(harness.charm.container, "can_connect", return_value=True):
            harness.charm._on_upgrade_charm(event)

        harness.charm._reconcile_database_schema.assert_not_called()
        event.defer.assert_not_called()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_on_upgrade_charm_defers_when_container_not_ready(self, harness: Harness):
        """When the container is unreachable the migration is skipped and the event is deferred."""
        harness.set_leader(True)
        harness.begin()
        harness.charm._reconcile_database_schema = MagicMock()
        event = MagicMock()

        with patch.object(harness.charm.container, "can_connect", return_value=False):
            harness.charm._on_upgrade_charm(event)

        harness.charm._reconcile_database_schema.assert_not_called()
        event.defer.assert_called_once()
        assert harness.charm.model.unit.status.__class__ is WaitingStatus

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_on_upgrade_charm_defers_on_transient_schema_failure(self, harness: Harness):
        """An undeterminable schema state (Waiting) defers the event so it is retried."""
        harness.set_leader(True)
        harness.begin()
        harness.charm._reconcile_database_schema = MagicMock(
            side_effect=ErrorWithStatus("cannot verify", WaitingStatus)
        )
        event = MagicMock()

        with patch.object(harness.charm.container, "can_connect", return_value=True):
            harness.charm._on_upgrade_charm(event)

        event.defer.assert_called_once()
        assert harness.charm.model.unit.status.__class__ is WaitingStatus

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_on_upgrade_charm_migration_failure_blocks_without_deferring(self, harness: Harness):
        """A genuine migration failure (Blocked) sets the status and is NOT deferred."""
        harness.set_leader(True)
        harness.begin()
        harness.charm._reconcile_database_schema = MagicMock(
            side_effect=ErrorWithStatus("boom", BlockedStatus)
        )
        event = MagicMock()

        with patch.object(harness.charm.container, "can_connect", return_value=True):
            harness.charm._on_upgrade_charm(event)

        assert harness.charm.model.unit.status.__class__ is BlockedStatus
        event.defer.assert_not_called()

    @patch(
        "charm.KubernetesServicePatch",
        lambda x, y, service_name, service_type, refresh_event: None,
    )
    def test_on_event_does_not_migrate_database_schema(self, harness: Harness):
        """A regular reconcile must NOT migrate the schema; that is scoped to upgrade-charm."""
        harness.begin()
        harness.charm._check_leader = MagicMock()
        harness.charm._get_interfaces = MagicMock(return_value={})
        harness.charm._check_no_conflicting_ingress_relations = MagicMock()
        harness.charm._ensure_bucket_exists = MagicMock()
        harness.charm._reconcile_database_schema = MagicMock()

        with patch("charm.update_layer", side_effect=ErrorWithStatus("stop", MaintenanceStatus)):
            with patch.object(harness.charm.container, "can_connect", return_value=True):
                harness.charm._on_event("some-event")

        harness.charm._reconcile_database_schema.assert_not_called()

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
    @patch(
        "charm.MlflowCharm._get_artifact_store_data", return_value=OBJECT_STORAGE_DATA_NORMALIZED
    )
    @patch("charm.MlflowCharm._get_backend_store_db_data", return_value=BACKEND_STORE_DB_DATA)
    @patch("charm.MlflowCharm._get_auth_database_db_data", return_value=AUTH_DATABASE_DB_DATA)
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
        _____: MagicMock,
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
    @patch(
        "charm.MlflowCharm._get_artifact_store_data", return_value=OBJECT_STORAGE_DATA_NORMALIZED
    )
    @patch("charm.MlflowCharm._get_backend_store_db_data", return_value=BACKEND_STORE_DB_DATA)
    @patch("charm.MlflowCharm._get_auth_database_db_data", return_value=AUTH_DATABASE_DB_DATA)
    @patch("charm.MlflowCharm._get_interfaces")
    @patch("charm.ServiceMeshConsumer")
    def test_multiple_ambient_ingress_relations(
        self,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        _____: MagicMock,
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
    @patch(
        "charm.MlflowCharm._get_artifact_store_data", return_value=OBJECT_STORAGE_DATA_NORMALIZED
    )
    @patch("charm.MlflowCharm._get_backend_store_db_data", return_value=BACKEND_STORE_DB_DATA)
    @patch("charm.MlflowCharm._get_auth_database_db_data", return_value=AUTH_DATABASE_DB_DATA)
    @patch("charm.MlflowCharm._get_interfaces")
    @patch("charm.ServiceMeshConsumer")
    def test_each_istio_ingress_route_relation_receives_config(
        self,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        _____: MagicMock,
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
    @patch(
        "charm.MlflowCharm._get_artifact_store_data", return_value=OBJECT_STORAGE_DATA_NORMALIZED
    )
    @patch("charm.MlflowCharm._get_backend_store_db_data", return_value=BACKEND_STORE_DB_DATA)
    @patch("charm.MlflowCharm._get_auth_database_db_data", return_value=AUTH_DATABASE_DB_DATA)
    @patch("charm.MlflowCharm._get_interfaces")
    @patch("charm.ServiceMeshConsumer")
    @pytest.mark.parametrize("config_submission_broken", [True, False], ids=["broken", "good"])
    def test_ambient_mode_ingress_configurations(
        self,
        _: MagicMock,
        __: MagicMock,
        ___: MagicMock,
        ____: MagicMock,
        _____: MagicMock,
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
    @patch(
        "charm.MlflowCharm._get_artifact_store_data", return_value=OBJECT_STORAGE_DATA_NORMALIZED
    )
    @patch("charm.MlflowCharm._get_backend_store_db_data", return_value=BACKEND_STORE_DB_DATA)
    @patch("charm.MlflowCharm._get_auth_database_db_data", return_value=AUTH_DATABASE_DB_DATA)
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
        _____: MagicMock,
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
