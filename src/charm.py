#!/usr/bin/env python3
# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

import base64
import logging
import secrets
from pathlib import Path
from typing import List, Optional, TypedDict
from urllib.parse import urlparse

import botocore.exceptions
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
from charmed_kubeflow_chisme.pebble import update_layer
from charmed_kubeflow_chisme.service_mesh import generate_allow_all_authorization_policy
from charms.data_platform_libs.v0.data_interfaces import DatabaseRequires
from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.istio_beacon_k8s.v0.service_mesh import (
    MeshType,
    PolicyResourceManager,
    ServiceMeshConsumer,
)
from charms.istio_ingress_k8s.v0.istio_ingress_route import (
    BackendRef,
    HTTPPathMatch,
    HTTPRoute,
    HTTPRouteMatch,
    IstioIngressRouteConfig,
    IstioIngressRouteRequirer,
    Listener,
    PathModifier,
    PathModifierType,
    ProtocolType,
    URLRewriteFilter,
    URLRewriteSpec,
)
from charms.kubeflow_dashboard.v0.kubeflow_dashboard_links import (
    DashboardLink,
    KubeflowDashboardLinksRequirer,
)
from charms.loki_k8s.v1.loki_push_api import LogForwarder
from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from charms.resource_dispatcher.v0.kubernetes_manifests import (
    KubernetesManifest,
    KubernetesManifestRequirerWrapper,
)
from jinja2 import Template
from lightkube import Client
from lightkube.models.core_v1 import ServicePort
from object_storage import S3Requirer
from ops import ActionEvent, SecretNotFoundError, main
from ops.charm import CharmBase
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import ExecError, Layer
from serialized_data_interface import (
    NoCompatibleVersions,
    NoVersionsListed,
    SerializedDataInterface,
    get_interfaces,
)
from serialized_data_interface.errors import RelationDataError

from services.s3 import S3BucketWrapper

INGRESS_MODES_TO_RELATION_NAMES = {
    "ambient": "istio-ingress-route",
    "sidecar": "ingress",
}
INGRESS_PATH_MATCHED_PREFIX = "/mlflow/"
INGRESS_PATH_REWRITTEN_PREFIX = "/"
METRICS_RELATION_NAME = "metrics-endpoint"
METRICS_PATH = "/metrics"
OBJECT_STORAGE_RELATION_NAME = "object-storage"
PODDEFAULTS_FILES = [
    "src/poddefaults/poddefault-minio.yaml.j2",
    "src/poddefaults/poddefault-mlflow.yaml.j2",
]
SECRETS_FILES = [
    "src/secrets/mlflow-minio-artifact.j2",
]
SERVICE_MESH_RELATION_NAME = "service-mesh"
# path inside the workload container where the artifact store's TLS CA bundle is written to be then
# referenced by the AWS_CA_BUNDLE environment variable, so that the tracking server can trust the
# store's TLS certificate - NOTE: under Pebble's home directory, writable by the non-root user:
S3_CA_BUNDLE_CONTAINER_PATH = "/var/lib/pebble/default/s3-ca-bundle.pem"

SCHEMA_OUT_OF_DATE_MARKER = "MLFLOW_SCHEMA_OUT_OF_DATE"
# read-only snippet run in the workload container to detect whether the tracking database schema is
# outdated with respect to the deployed MLflow version, based on this exception being raised by
# running `_verify_schema` directly:
# https://github.com/mlflow/mlflow/blob/v3.15.1/mlflow/store/db/utils.py#L127-L134
# NOTE:
# - it is and it has to be idempotent and read-only
# - it relies on MLflow's own schema verification, which raises a stable error instructing to run
#   `mlflow db upgrade` when the schema is out of date while treating any other outcome
#   (up-to-date, empty/uninitialised or undeterminable state) as "not out of date"
# - it is necessary as the `mlflow` CLI does not provide any read-only command to achieve the same
#   result (`mlflow db upgrade` is not read-only)
SCHEMA_CHECK_SNIPPET = f"""\
import sys
from mlflow.store.db.utils import _verify_schema, create_sqlalchemy_engine_with_retry

engine = create_sqlalchemy_engine_with_retry(sys.argv[1])
try:
    _verify_schema(engine)
except Exception as exc:
    message = str(exc).lower()
    if 'Detected out-of-date database schema'.lower() in message:
        print('{SCHEMA_OUT_OF_DATE_MARKER}')
        sys.exit(0)
    raise
"""

# TODO: remove once this issue is fixed: https://github.com/mlflow/mlflow/issues/19943
# snippet run in the workload container to permit the relation user to create the immutability
# trigger that MLflow's schema migration adds alongside the `secrets` table, as with binary logging
# enabled (the default on mysql-k8s), MySQL rejects `CREATE TRIGGER` for a user lacking the
# SET_USER_ID/SUPER privilege (error 1419) unless the global `log_bin_trust_function_creators`
# variable is set - the relation user requests the `charmed_dba` role, which grants
# SYSTEM_VARIABLES_ADMIN, so the charm can itself persist that variable using the relation
# credentials and no administrative (root) access to the database is required - notably,
# `SET PERSIST` keeps the setting across server restarts, and the statement is idempotent:
ENABLE_TRIGGER_CREATION_SNIPPET = """\
import sys
from sqlalchemy import create_engine, text

engine = create_engine(sys.argv[1])
with engine.connect() as connection:
    connection.execute(text('SET PERSIST log_bin_trust_function_creators = ON'))
"""

# directory inside the workload container where the charm writes the RBAC/auth files it owns: the
# rendered basic_auth.ini and the custom authentication module - NOTE: under Pebble's home
# directory, writable by the non-root user:
AUTH_CONFIG_DIR = "/var/lib/pebble/default/auth"
AUTH_CONFIG_CONTAINER_PATH = f"{AUTH_CONFIG_DIR}/basic_auth.ini"
AUTH_MODULE_NAME = "custom_userid_header_auth"
AUTH_MODULE_CONTAINER_PATH = f"{AUTH_CONFIG_DIR}/{AUTH_MODULE_NAME}.py"
AUTH_MODULE_SOURCE_PATH = "src/auth/custom_userid_header_auth.py"
AUTH_CONFIG_TEMPLATE_PATH = "src/auth/basic_auth.ini.j2"
AUTHORIZATION_FUNCTION = f"{AUTH_MODULE_NAME}:authenticate_request"

# username of the charm's MLflow super-admin - NOTE: it contains an underscore and it does not
# contain any "@" so that it can never collide with a K8s namespace name (DNS-1123) or an IAM
# email, which are the possible identity value kinds set by external entities:
MLFLOW_SUPER_ADMIN_USERNAME = "mlflow_charm_super_admin"

# label of the application-scoped Juju secret holding the auto-generated RBAC credentials (the
# Flask secret key and the super-admin password), kept stable across restarts and identical across
# replicas:
AUTH_SECRET_LABEL = "mlflow-auth-credentials"

RELATION_ENDPOINT_FOR_BACKEND_STORE_DB = "relational-db"


# Normalized artifact store data returned by MlflowCharm._get_artifact_store_data, covering
# both the `object-storage` and `s3` interfaces.
class ArtifactStoreData(TypedDict):
    access_key: str
    secret_key: str
    host: str
    port: int
    secure: bool
    region: str
    bucket: str
    tls_ca_chain: Optional[List[str]]
    is_s3: bool


class MlflowCharm(CharmBase):
    """A Juju Charm for MLFlow."""

    def __init__(self, *args):
        super().__init__(*args)

        self.logger = logging.getLogger(__name__)
        self._mlflow_port = int(self.model.config["mlflow_port"])
        self._service_name = self.model.app.name
        self._namespace = self.model.name
        self._exporter_port = self.model.config["mlflow_prometheus_exporter_port"]
        self._container_name = "mlflow-server"
        self._exporter_container_name = "mlflow-prometheus-exporter"
        self._backend_store_database_name = "mlflow"
        self._container = self.unit.get_container(self._container_name)
        self._exporter_container = self.unit.get_container(self._exporter_container_name)
        self.backend_store_database = DatabaseRequires(
            self,
            relation_name=RELATION_ENDPOINT_FOR_BACKEND_STORE_DB,
            database_name=self._backend_store_database_name,
            # NOTE: `charmed_dba` grants the relation user SYSTEM_VARIABLES_ADMIN (to persist
            # `log_bin_trust_function_creators`) and TRIGGER, which together let MLflow's schema
            # migration create the `secrets` immutability trigger under binary logging
            # TODO: remove once this issue is fixed: https://github.com/mlflow/mlflow/issues/19943
            extra_user_roles="charmed_dba",
        )

        self._secrets_manifests_wrapper = None
        self._poddefaults_manifests_wrapper = None

        self.framework.observe(self.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(self.on.config_changed, self._on_event)
        self.framework.observe(self.on.mlflow_server_pebble_ready, self._on_pebble_ready)

        for rel in self.model.relations.keys():
            self.framework.observe(self.on[rel].relation_changed, self._on_event)
        self._create_service()

        self.framework.observe(self.on.update_status, self._on_event)
        self.framework.observe(self.backend_store_database.on.database_created, self._on_event)
        self.framework.observe(self.backend_store_database.on.endpoints_changed, self._on_event)
        self.framework.observe(
            self.on.backend_store_db_relation_broken,
            self._on_backend_store_relation_removed,
        )

        self.framework.observe(
            self.on.get_minio_credentials_action, self._on_get_minio_credentials
        )

        self.framework.observe(self.on.remove, self._remove_authorization_policies)

        self.framework.observe(
            self.on[SERVICE_MESH_RELATION_NAME].relation_broken,
            self._remove_authorization_policies,
        )

        # Log forwarding to Loki
        self._logging = LogForwarder(charm=self)

        # Prometheus related config
        self.prometheus_provider = MetricsEndpointProvider(
            charm=self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "metrics_path": METRICS_PATH,
                    "static_configs": [
                        {
                            "targets": [
                                "*:{}".format(self._mlflow_port),
                                "*:{}".format(
                                    self.model.config["mlflow_prometheus_exporter_port"]
                                ),
                            ]
                        }
                    ],
                }
            ],
        )
        self.dashboard_provider = GrafanaDashboardProvider(
            charm=self,
            relation_name="grafana-dashboard",
        )

        # add link in kubeflow-dashboard sidebar
        self.kubeflow_dashboard_sidebar = KubeflowDashboardLinksRequirer(
            charm=self,
            relation_name="dashboard-links",
            dashboard_links=[
                DashboardLink(
                    text="MLflow",
                    link=INGRESS_PATH_MATCHED_PREFIX,
                    type="item",
                    icon="device:data-usage",
                    location="external",
                )
            ],
        )

        # for an ambient-mode service mesh:

        self._mesh = ServiceMeshConsumer(self)

        # Allow all policy needed to allow requests from all user namespaces
        self._allow_all_policy = generate_allow_all_authorization_policy(
            app_name=self.app.name,
            namespace=self.model.name,
        )

        self.ambient_mode_ingress = IstioIngressRouteRequirer(
            self, relation_name=INGRESS_MODES_TO_RELATION_NAMES["ambient"]
        )

        self.framework.observe(
            self.ambient_mode_ingress.on.ready, self._on_ambient_mode_ingress_ready
        )

        # object-storage and s3-credentials relation_changed events are already observed by the
        # generic loop above; only their relation_broken events need to be observed here.
        self.framework.observe(self.on["object-storage"].relation_broken, self._on_event)
        self.framework.observe(self.on["s3-credentials"].relation_broken, self._on_event)

        self.s3 = S3Requirer(self, relation_name="s3-credentials")

    @property
    def container(self):
        """Return container."""
        return self._container

    @property
    def exporter_container(self):
        """Return container."""
        return self._exporter_container

    @property
    def _ingress_config(self):
        if self.ambient_mode_ingress.tls_enabled:
            http_listener = Listener(
                port=443,  # expecting ingress traffic over HTTPS via the default port: 443
                protocol=ProtocolType.HTTP,  # expecting ingress traffic via HTTP
                # NOTE: listener name auto-generated by the charm
            )
        else:
            http_listener = Listener(
                port=80,  # expecting ingress traffic to come through the default HTTP port: 80
                protocol=ProtocolType.HTTP,  # expecting ingress traffic via HTTP
                # NOTE: listener name auto-generated by the charm
            )

        return IstioIngressRouteConfig(
            model=self._namespace,  # NOTE: requirer's namespace, where target services live
            listeners=[http_listener],
            http_routes=[
                # resource of kind `HTTPRoute.gateway.networking.k8s.io`:
                # https://gateway-api.sigs.k8s.io/reference/spec/#httproute
                HTTPRoute(
                    name="http-route",
                    listener=http_listener,
                    matches=[
                        HTTPRouteMatch(path=HTTPPathMatch(value=INGRESS_PATH_MATCHED_PREFIX))
                    ],
                    filters=[
                        URLRewriteFilter(
                            urlRewrite=URLRewriteSpec(
                                path=PathModifier(
                                    type=PathModifierType.ReplacePrefixMatch,
                                    value=INGRESS_PATH_REWRITTEN_PREFIX,
                                )
                            )
                        )
                    ],
                    backends=[BackendRef(service=self._service_name, port=self._mlflow_port)],
                ),
            ],
        )

    @property
    def _policy_resource_manager(self) -> PolicyResourceManager:
        """Create and return PolicyResourceManager, used to manage authorization policies."""
        return PolicyResourceManager(
            charm=self,
            lightkube_client=Client(field_manager=f"{self.app.name}-{self.model.name}"),
            labels={
                "app.kubernetes.io/instance": f"{self.app.name}-{self.model.name}",
                "kubernetes-resource-handler-scope": f"{self.app.name}-allow-all",
            },
            logger=self.logger,
        )

    @property
    def secrets_manifests_wrapper(self):
        if not self._secrets_manifests_wrapper:
            self._secrets_manifests_wrapper = KubernetesManifestRequirerWrapper(
                charm=self, relation_name="secrets"
            )
        return self._secrets_manifests_wrapper

    @property
    def poddefaults_manifests_wrapper(self):
        if not self._poddefaults_manifests_wrapper:
            self._poddefaults_manifests_wrapper = KubernetesManifestRequirerWrapper(
                charm=self, relation_name="pod-defaults"
            )
        return self._poddefaults_manifests_wrapper

    def _create_service(self):
        """Create k8s service based on charm'sconfig."""
        if self.config["enable_mlflow_nodeport"]:
            service_type = "NodePort"
            self._node_port = self.model.config["mlflow_nodeport"]
            self._exporter_node_port = self.model.config["mlflow_prometheus_exporter_nodeport"]
            port = ServicePort(
                self._mlflow_port,
                name=f"{self.app.name}",
                targetPort=self._mlflow_port,
                nodePort=int(self._node_port),
            )

            exporter_port = ServicePort(
                int(self._exporter_port),
                name=f"{self.app.name}-prometheus-exporter",
                targetPort=int(self._exporter_port),
                nodePort=int(self._exporter_node_port),
            )
        else:
            service_type = "ClusterIP"
            port = ServicePort(self._mlflow_port, name=f"{self.app.name}")
            exporter_port = ServicePort(
                int(self._exporter_port), name=f"{self.app.name}-prometheus-exporter"
            )
        self.service_patcher = KubernetesServicePatch(
            self,
            [port, exporter_port],
            service_type=service_type,
            service_name=self._service_name,
            refresh_event=self.on.config_changed,
        )

    @property
    def service_environment(self):
        """Return environment variables based on model configuration."""
        return self._generate_environment()

    @property
    def _mlflow_server_layer(self) -> Layer:
        """Create and return Pebble framework layer."""

        layer_config = {
            "summary": "mlflow-server layer",
            "description": "Pebble config layer for mlflow-server",
            "services": {
                self._container_name: {
                    "override": "replace",
                    "summary": "Entrypoint of mlflow-server image",
                    # running the tracking server while enabling RBAC via the "basic-auth" app:
                    "command": "mlflow server --app-name basic-auth",
                    "startup": "enabled",
                    "environment": self.service_environment,  # defaults `mlflow server` CLI options
                }
            },
        }

        return Layer(layer_config)

    @property
    def _mlflow_exporter_layer(self) -> Layer:
        """Create and return Pebble framework layer."""

        layer_config = {
            "summary": "mlflow-prometheus-exporter layer",
            "description": "Pebble config layer for mlflow-prometheus-exporter",
            "services": {
                self._exporter_container_name: {
                    "override": "replace",
                    "summary": "Entrypoint of mlflow-prometheus-exporter image",
                    "command": (
                        "python3 "
                        "mlflow_exporter.py "
                        f"--port {self._exporter_port} "
                        f"--mlflowurl http://localhost:{self._mlflow_port}/"
                    ),
                    "startup": "enabled",
                },
            },
        }

        return Layer(layer_config)

    @property
    def secrets_context(self) -> dict:
        try:
            interfaces = self._get_interfaces()
            artifact_store_data = self._get_artifact_store_data(interfaces)
        except ErrorWithStatus as error:
            self.logger.error("Failed to generate container configuration.")
            raise error
        tls_ca_chain = artifact_store_data["tls_ca_chain"]
        secrets_context = {
            "app_name": self.app.name,
            "access_key": artifact_store_data["access_key"],
            "secret_access_key": artifact_store_data["secret_key"],
            "is_proxy_mode_enabled": self.proxy_mode,
            # base64-encoded S3 CA bundle for clients to directly trust the artifact store when in
            # no proxy mode and when private TLS certificates are used:
            "s3_ca_bundle_b64": (
                base64.b64encode("\n".join(tls_ca_chain).encode()).decode() if tls_ca_chain else ""
            ),
        }
        return secrets_context

    @property
    def poddefaults_context(self) -> dict:
        try:
            interfaces = self._get_interfaces()
            artifact_store_data = self._get_artifact_store_data(interfaces)
        except ErrorWithStatus as error:
            self.logger.error("Failed to generate container configuration.")
            raise error
        poddefaults_context = {
            "app_name": self.app.name,
            "s3_endpoint": self._extract_s3_endpoint(artifact_store_data),
            "mlflow_endpoint": (
                f"http://{self.app.name}.{self._namespace}.svc.cluster.local:"
                f"{self._mlflow_port}"
            ),
            "is_proxy_mode_enabled": self.proxy_mode,
            # whether to mount the S3 CA bundle into client pods and point AWS_CA_BUNDLE at it, so
            # their direct (no-proxy mode) connections trust the artifact store's TLS certificate:
            "s3_ca_bundle_present": bool(artifact_store_data["tls_ca_chain"]),
        }
        return poddefaults_context

    @property
    def proxy_mode(self) -> bool:
        """Return whether the tracking server acts as a proxy to the artifact store."""
        return self.model.config["serve_artifacts"]

    def _get_interfaces(self):
        """Retrieve interface object."""
        try:
            interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            raise ErrorWithStatus(err, WaitingStatus)
        except NoCompatibleVersions as err:
            raise ErrorWithStatus(err, BlockedStatus)
        return interfaces

    def _get_backend_store_db_data(self) -> dict:
        mysql_relation = self.model.get_relation(RELATION_ENDPOINT_FOR_BACKEND_STORE_DB)

        # Raise exception and stop execution if the backend-store relation is not established
        if not mysql_relation:
            raise ErrorWithStatus(
                f"Please add the relation {RELATION_ENDPOINT_FOR_BACKEND_STORE_DB}", BlockedStatus
            )

        data = self.backend_store_database.fetch_relation_data()
        self.logger.debug("Got following database data: %s", data)
        for val in data.values():
            if not val:
                continue
            try:
                host, port = val["endpoints"].split(":")
                db_data = {
                    "host": host,
                    "port": port,
                    "username": val["username"],
                    "password": val["password"],
                }
            except KeyError:
                raise ErrorWithStatus(
                    f"Incorrect data found in relation {RELATION_ENDPOINT_FOR_BACKEND_STORE_DB}",
                    WaitingStatus,
                )
            return db_data
        raise ErrorWithStatus(
            f"Waiting for {RELATION_ENDPOINT_FOR_BACKEND_STORE_DB} relation data", WaitingStatus
        )

    def _get_backend_store_uri(self) -> str:
        """Return the SQLAlchemy backend-store URI from the MySQL provider.

        Raises:
            ErrorWithStatus if the relation or its data are not ready.
        """
        backend_store_data = self._get_backend_store_db_data()
        return (
            f"mysql+pymysql://{backend_store_data['username']}:{backend_store_data['password']}"
            f"@{backend_store_data['host']}:{backend_store_data['port']}"
            f"/{self._backend_store_database_name}"
        )

    def _is_database_schema_out_of_date(self, backend_store_uri: str) -> bool:
        """Return True only if MLflow reports the tracking DB schema as out of date.

        Runs MLflow's own schema verification in the workload container via a custom Python code
        snippet (necessary as the `mlflow` CLI does not provide any read-only command to achieve
        the same result). Any outcome other than a positively detected out-of-date schema (up to
        date or empty/uninitialised DB) returns False, so that unrelated failures are never
        misattributed to a required database migration and the caller can defer and retry.

        Raises:
            ErrorWithStatus(..., Waiting) when the schema check could not be completed, so the
            caller can defer and retry rather than proceed on incomplete information: the database
            may not be reachable yet, or the check could not complete (unexpected MLflow error or a
            failed exec).
        """
        try:
            proc = self.container.exec(["python3", "-c", SCHEMA_CHECK_SNIPPET, backend_store_uri])
            stdout, _ = proc.wait_output()

        except ExecError as error:
            self.logger.warning(
                "Could not complete the database schema check "
                f"(database unreachable, unexpected MLflow error or failed exec): "
                f"exit code {error.exit_code}",
            )
            raise ErrorWithStatus(
                "The database is not yet reachable, or the schema check could not be completed; "
                "will retry.",
                WaitingStatus,
            )

        return SCHEMA_OUT_OF_DATE_MARKER in stdout

    def _run_database_migration(self, database_uri: str) -> None:
        """Run `mlflow db upgrade` in the workload container to migrate the tracking DB schema.

        The `mlflow db upgrade` command is idempotent, so it is safe to run whenever the schema is
        detected as out of date.

        Raises:
            ErrorWithStatus(..., Blocked) if the migration command fails, so the distinctive
            failure is clearly attributable to the database migration rather than an unrelated
            workload problem.

        Command reference:
            https://mlflow.org/docs/latest/api_reference/cli.html#mlflow-db-upgrade
        """
        self.logger.info("Running 'mlflow db upgrade' database schema migration.")

        try:
            process = self.container.exec(["mlflow", "db", "upgrade", database_uri])
            process.wait_output()

        except ExecError as error:
            # keeping the Juju status message short; the failure detail and remediation go to the
            # logs - NOTE: the raw stderr is deliberately NOT logged as it can embed the backend
            # store URI (and thus database credentials); only the exit code is safe to log:
            self.logger.error(
                "Database schema migration ('mlflow db upgrade') failed with exit code "
                f"{error.exit_code}. The schema may be left partially migrated: 'mlflow db "
                "upgrade' applies Alembic migrations and MySQL DDL is not transactional, so a "
                "failed step cannot be rolled back automatically. Restore the tracking database "
                "from a backup taken before the refresh (see the charm's backup/restore how-to), "
                "then retry the refresh."
            )
            raise ErrorWithStatus(
                "Database schema migration failed. Check the unit logs and act accordingly.",
                # NOTE: Blocked (not Waiting) so the caller does not defer/retry: the container and
                # database are already reachable and the schema was determinable, so a failing
                # `mlflow db upgrade` is a non-transient problem (e.g., bad/partial schema,
                # incompatible version jump, insufficient privileges) that needs operator
                # intervention rather than a retry:
                BlockedStatus,
            )

        self.logger.info("Database schema migration completed successfully.")

    # TODO: remove once this issue is fixed: https://github.com/mlflow/mlflow/issues/19943
    def _ensure_trigger_creation_allowed(self, backend_store_uri: str) -> None:
        """Allow the MySQL relation user to create MLflow's `secrets` immutability trigger.

        Workaround for upstream MLflow bug https://github.com/mlflow/mlflow/issues/19943: MLflow's
        schema migration unconditionally issues a `CREATE TRIGGER` for the `secrets` table. With
        binary logging enabled (the mysql-k8s default), MySQL rejects that statement for a user
        without SET_USER_ID/SUPER (error 1419) unless the global `log_bin_trust_function_creators`
        variable is set. The relation user is granted the `charmed_dba` role (SYSTEM_VARIABLES_
        ADMIN), so the charm persists that variable itself using the relation credentials - no root
        access to the database is needed. The statement is idempotent and must run before the
        workload initialises or migrates the schema.

        Raises:
            ErrorWithStatus(..., Waiting) when the variable can't be set (e.g., the database is not
            yet reachable), so the caller can defer and retry.
        """
        try:
            proc = self.container.exec(
                ["python3", "-c", ENABLE_TRIGGER_CREATION_SNIPPET, backend_store_uri]
            )
            proc.wait_output()

        except ExecError as error:
            stderr = error.stderr or ""
            self.logger.warning(
                f"Could not enable trigger creation on the database: exit code {error.exit_code}",
            )

            # MySQL error 1227 = the user lacks SUPER/SYSTEM_VARIABLES_ADMIN. This happens when the
            # relation user was not created with the `charmed_dba` role - notably on an in-place
            # upgrade from a revision that did not request it, since the data-platform provider only
            # grants extra roles when the relation (and its user) is first created. Retrying will
            # never succeed, so surface an actionable Blocked status instead of looping in Waiting.
            if "1227" in stderr or "SYSTEM_VARIABLES_ADMIN" in stderr:
                # keeping the Juju status message short; the remediation details go to the logs:
                self.logger.error(
                    "The database user lacks the SYSTEM_VARIABLES_ADMIN privilege required to "
                    "migrate the schema. Remove and re-add the "
                    f"'{RELATION_ENDPOINT_FOR_BACKEND_STORE_DB}' relation (or grant the "
                    "'charmed_dba' role to the database user) so the charm can proceed."
                )
                raise ErrorWithStatus(
                    "Database user lacks privileges to migrate the schema. Check the unit logs "
                    "and act accordingly.",
                    BlockedStatus,
                )

            raise ErrorWithStatus(
                "Could not prepare the database for schema migration; will retry.", WaitingStatus
            )

    def _reconcile_database_schema(self) -> None:
        """Automatically migrate the tracking database schema when it is out of date.

        Triggered as part of the reconcile logic, notably on the `upgrade-charm` (Juju refresh)
        event, so that a charm refresh shipping an MLflow release with schema changes migrates the
        database without manual intervention. A distinctive Maintenance status is set while the
        migration runs.
        """
        backend_store_uri = self._get_backend_store_uri()

        # TODO: remove once this issue is fixed: https://github.com/mlflow/mlflow/issues/19943
        self._ensure_trigger_creation_allowed(backend_store_uri)

        if self._is_database_schema_out_of_date(backend_store_uri):
            self.unit.status = MaintenanceStatus(
                "Database schema is out of date for the deployed MLflow version; migrating it."
            )
            self._run_database_migration(backend_store_uri)

    def _validate_sdi_interface(self, interfaces, relation_name, default_return=None):
        """Validates data received from SerializedDataInterface, returning the data if valid.

        Optionally can return a default_return value when no relation is established

        Raises:
            ErrorWithStatus(..., Blocked) when no relation established (unless default_return set)
            ErrorWithStatus(..., Blocked) if interface is not using SDI
            ErrorWithStatus(..., Blocked) if data in interface fails schema check
            ErrorWithStatus(..., Waiting) if we have a relation established but no data passed

        Params:
            interfaces:

        Returns:
              (dict) interface data
        """
        # If nothing is related to this relation, return a default value or raise an error
        if relation_name not in interfaces or interfaces[relation_name] is None:
            if default_return is not None:
                return default_return
            else:
                raise ErrorWithStatus(
                    f"Please add required relation {relation_name}", BlockedStatus
                )

        relations = interfaces[relation_name]
        if not isinstance(relations, SerializedDataInterface):
            raise ErrorWithStatus(
                f"Unexpected error with {relation_name} relation data - data not as expected",
                BlockedStatus,
            )

        # Get and validate data from the relation
        try:
            # relations is a dict of {(ops.model.Relation, ops.model.Application): data}
            unpacked_relation_data = relations.get_data()
        except RelationDataError as val_error:
            # Validation in .get_data() ensures if data is populated, it matches the schema and is
            # not incomplete
            self.logger.error(val_error)
            raise ErrorWithStatus(
                f"Found incomplete/incorrect relation data for {relation_name}. See logs",
                BlockedStatus,
            )

        # Check if we have an established relation with no data exchanged
        if len(unpacked_relation_data) == 0:
            raise ErrorWithStatus(f"Waiting for {relation_name} relation data", WaitingStatus)

        # Unpack data (we care only about the first element)
        data_dict = list(unpacked_relation_data.values())[0]

        # Catch if empty data dict is received (JSONSchema ValidationError above does not raise
        # when this happens)
        # Remove once addressed in:
        # https://github.com/canonical/serialized-data-interface/issues/28
        if len(data_dict) == 0:
            raise ErrorWithStatus(
                f"Found empty relation data for {relation_name}",
                BlockedStatus,
            )

        return data_dict

    def _get_object_storage_data(self, interfaces):
        """Retrieve object-storage relation data."""
        relation_name = "object-storage"
        return self._validate_sdi_interface(interfaces, relation_name)

    def _get_s3_data(self) -> dict:
        """Retrieve and validate data from the s3-credentials relation.

        Raises:
            ErrorWithStatus(..., Waiting) if the relation exists but required data
                (access-key, secret-key, endpoint) is not yet available.
        """
        relation = self.model.get_relation("s3-credentials")
        info = self.s3.get_storage_connection_info(relation)
        required_fields = ("access-key", "secret-key", "endpoint")
        if not info:
            raise ErrorWithStatus("Waiting for s3-credentials relation data", WaitingStatus)
        missing = [field for field in required_fields if not info.get(field)]
        if missing:
            raise ErrorWithStatus(
                f"Waiting for s3-credentials relation data, missing fields: {', '.join(missing)}",
                WaitingStatus,
            )
        return info

    def _get_artifact_store_data(self, interfaces=None) -> ArtifactStoreData:
        """Return normalized artifact store data from the active storage relation.

        Supports both the `object-storage` and `s3` interfaces, returning a common dict with
        keys: access_key, secret_key, host, port, secure, region, bucket, tls_ca_chain, is_s3.

        Exactly one of the `object-storage` or `s3-credentials` relations is expected.

        Raises:
            ErrorWithStatus(..., Blocked) if both relations are established at once.
            ErrorWithStatus(..., Blocked) if neither relation is established.
            ErrorWithStatus(..., Waiting) if the active relation has no data yet.
        """
        has_object_storage = self.model.relations["object-storage"]
        has_s3 = self.model.relations["s3-credentials"]

        if has_object_storage and has_s3:
            raise ErrorWithStatus(
                "Too many object storage relations. Please relate to only one of "
                "`object-storage` or `s3-credentials`.",
                BlockedStatus,
            )

        if not has_object_storage and not has_s3:
            raise ErrorWithStatus(
                "Missing object storage relation. Please relate to one of "
                "`object-storage` or `s3-credentials`.",
                BlockedStatus,
            )

        if has_s3:
            data = self._get_s3_data()
            host, port, secure = self._parse_s3_endpoint(data["endpoint"])
            if not host:
                raise ErrorWithStatus(
                    f"Invalid s3 endpoint: {data['endpoint']!r}",
                    WaitingStatus,
                )
            access_key = data["access-key"]
            secret_key = data["secret-key"]
            region = data.get("region", "")
            bucket = data.get("bucket", "")
            tls_ca_chain = data.get("tls-ca-chain")
        else:
            if interfaces is None:
                interfaces = self._get_interfaces()
            obj = self._get_object_storage_data(interfaces)
            access_key = obj["access-key"]
            secret_key = obj["secret-key"]
            host = f"{obj['service']}.{obj['namespace']}"
            port = obj["port"]
            secure = obj["secure"]
            region = ""
            bucket = ""
            tls_ca_chain = None

        return ArtifactStoreData(
            access_key=access_key,
            secret_key=secret_key,
            host=host,
            port=port,
            secure=secure,
            region=region,
            bucket=bucket,
            tls_ca_chain=tls_ca_chain,
            is_s3=bool(has_s3),
        )

    @staticmethod
    def _extract_s3_endpoint(artifact_store_data: ArtifactStoreData) -> str:
        """Extract the s3 endpoint URL from the artifact store data."""
        scheme = "https" if artifact_store_data["secure"] else "http"
        return f"{scheme}://{artifact_store_data['host']}:{artifact_store_data['port']}"

    @staticmethod
    def _parse_s3_endpoint(endpoint: str) -> tuple:
        """Parse an s3 endpoint into a (host, port, secure) tuple.

        The endpoint may be a full URL (e.g. "https://s3.example.com:443") or a bare
        "host[:port]". The charm needs the host, port and TLS flag as separate values.

        When a URL scheme is present it determines TLS and the default port.
        When only a bare host[:port] is given, TLS is inferred from the port:
          - 443 -> HTTPs
          - Otherwise -> HTTP
        """
        parsed_endpoint = urlparse(endpoint if "://" in endpoint else f"//{endpoint}")
        if parsed_endpoint.scheme:
            secure = True if parsed_endpoint.scheme == "https" else False
            port = parsed_endpoint.port or (443 if secure else 80)
        else:
            # bare host[:port]: infer TLS from port
            port = parsed_endpoint.port or 80
            secure = True if port == 443 else False
        return parsed_endpoint.hostname, port, secure

    def _on_get_minio_credentials(self, event: ActionEvent):
        """Returns the credentials for minio as an action response."""
        try:
            artifact_store_data = self._get_artifact_store_data()
            event.set_results(
                {
                    "access-key": artifact_store_data["access_key"],
                    "secret-access-key": artifact_store_data["secret_key"],
                }
            )
        except ErrorWithStatus:
            event.fail("Minio is not reachable yet. Please try again in a few minutes.")

    def _resolve_bucket_name(self, obj: dict) -> str:
        """Return the object storage bucket name from the relation or config.

        The bucket name comes from the active object storage relation:
        - For s3-credentials, either through the provider side (s3-integrator) or through the
            `default_artifact_root` config option. Provider side takes precedence.
        - For object-storage, through the `default_artifact_root` config option.

        Raises:
            ErrorWithStatus(..., Blocked) if no bucket name is available.
        """
        if obj["bucket"]:
            return obj["bucket"]

        bucket_name = self.model.config["default_artifact_root"]
        if bucket_name:
            relation_name = "s3-credentials" if obj["is_s3"] else "object-storage"
            self.logger.info(
                f"{relation_name} relation doesn't provide a bucket; using the "
                f"'default_artifact_root' config option: '{bucket_name}'."
            )
            return bucket_name

        raise ErrorWithStatus(
            "No object storage bucket name available. Set the 'default_artifact_root' "
            "config option or provide a bucket through the s3-credentials relation.",
            BlockedStatus,
        )

    def _ensure_bucket_exists(self) -> None:
        """Ensure bucket on object storage exists by using a boto3 client."""
        artifact_store_data = self._get_artifact_store_data()

        s3_wrapper = S3BucketWrapper(
            access_key=artifact_store_data.get("access_key"),
            secret_access_key=artifact_store_data.get("secret_key"),
            s3_service=artifact_store_data["host"],
            s3_port=artifact_store_data["port"],
            secure=artifact_store_data["secure"],
            region=artifact_store_data["region"],
            tls_ca_chain=artifact_store_data.get("tls_ca_chain"),
        )

        bucket_name = self._resolve_bucket_name(artifact_store_data)
        try:
            self.unit.status = MaintenanceStatus(f"Checking if bucket {bucket_name} exists.")
            # Check if bucket already exists
            if s3_wrapper.bucket_exists(bucket_name):
                self.model.unit.status = ActiveStatus()
                return

            # Create the bucket if missing
            self.unit.status = MaintenanceStatus(f"Creating bucket {bucket_name}.")
            s3_wrapper.create_bucket(bucket_name)
            self.model.unit.status = ActiveStatus()
            return

        except botocore.exceptions.SSLError as e:
            msg = "Object storage TLS verification failed. Check CA chain configuration."
            self.logger.error(f"{msg}: {e}")
            raise ErrorWithStatus(msg, BlockedStatus)
        except (
            botocore.exceptions.ClientError,
            botocore.exceptions.ConnectTimeoutError,
            botocore.exceptions.ReadTimeoutError,
            botocore.exceptions.EndpointConnectionError,
        ) as e:
            msg = "Waiting for object storage to become accessible."
            self.logger.warning(f"{msg}: {e}")
            raise ErrorWithStatus(msg, WaitingStatus)

    def _reconcile_s3_ca_bundle(self, artifact_store_data: ArtifactStoreData) -> None:
        """Push the artifact store's TLS CA bundle into the workload container when required.

        In proxy mode the tracking server directly accesses the S3 store, so it must trust the
        store's TLS CA. The bundle is written to the path referenced by the AWS_CA_BUNDLE
        environment variable set in `_generate_environment`, a variable that the boto3 client that
        the tracking server relies on behind the scenes.

        When the tracking server is not in proxy mode or when the artifact store does not have a
        private CA chain, this is not necessary.
        """
        tls_ca_chain = artifact_store_data["tls_ca_chain"]
        if self.proxy_mode and tls_ca_chain:
            self.container.push(
                S3_CA_BUNDLE_CONTAINER_PATH, "\n".join(tls_ca_chain), make_dirs=True
            )

    def _check_leader(self):
        """Check if this unit is a leader."""
        if not self.unit.is_leader():
            self.logger.info("Not a leader, skipping setup")
            raise ErrorWithStatus("Waiting for leadership", WaitingStatus)

    def _check_no_conflicting_ingress_relations(self) -> None:
        """Check that ambient-mode and sidecar-mode ingress relations are not both set.

        Each endpoint may hold any number of relations, so this inspects the full list
        of relations on each endpoint rather than assuming at most one.
        """
        ambient_relations = self.model.relations[INGRESS_MODES_TO_RELATION_NAMES["ambient"]]
        sidecar_relations = self.model.relations[INGRESS_MODES_TO_RELATION_NAMES["sidecar"]]

        if ambient_relations and sidecar_relations:
            self.logger.error(
                f"Both '{INGRESS_MODES_TO_RELATION_NAMES["ambient"]}' and "
                f"'{INGRESS_MODES_TO_RELATION_NAMES["sidecar"]}' relations are present."
            )
            raise ErrorWithStatus(
                (
                    f"Cannot have both '{INGRESS_MODES_TO_RELATION_NAMES["ambient"]}' and "
                    f"'{INGRESS_MODES_TO_RELATION_NAMES["sidecar"]}' relations at the same time, "
                    "remove one to unblock."
                ),
                BlockedStatus,
            )

    def _generate_environment(self) -> dict:
        """Return environment variables for the `mlflow server` command.

        See here how such environment variables provide defaults for `mlflow server` CLI options:
        https://mlflow.org/docs/3.15.1/api_reference/cli.html#mlflow-server
        """
        try:
            interfaces = self._get_interfaces()
            artifact_store_data = self._get_artifact_store_data(interfaces)
            backend_store_uri = self._get_backend_store_uri()
            auth_secrets = self._get_or_create_auth_secrets()
        except ErrorWithStatus as error:
            self.logger.error("Failed to generate container configuration.")
            raise error

        s3_bucket_uri = f"s3://{self._resolve_bucket_name(artifact_store_data)}"

        environment_variables = {
            "MLFLOW_BACKEND_STORE_URI": backend_store_uri,
            "MLFLOW_EXPOSE_PROMETHEUS": METRICS_PATH,
            "MLFLOW_HOST": "0.0.0.0",
            "MLFLOW_PORT": self._mlflow_port,
            # NOTE: security middleware disable as already provided by the outer Istio layer:
            # https://mlflow.org/docs/latest/self-hosting/security/network/#disable-security-middleware  # noqa: E501
            "MLFLOW_SERVER_DISABLE_SECURITY_MIDDLEWARE": "true",
            # enabling workspaces (tenants):
            "MLFLOW_ENABLE_WORKSPACES": "true",
            # disabling seeding new workspaces with default roles, as the charm manages them
            # exclusively and explicitly:
            "MLFLOW_RBAC_SEED_DEFAULT_ROLES": "false",
            # the charm-rendered basic_auth.ini (RBAC settings and custom authentication logic):
            "MLFLOW_AUTH_CONFIG_PATH": AUTH_CONFIG_CONTAINER_PATH,
            # static and identical across replicas, required by the auth app for CSRF/session:
            "MLFLOW_FLASK_SERVER_SECRET_KEY": auth_secrets["flask_secret_key"],
            # trusted user-ID header the custom authentication logic reads to map requests to users:
            "IDENTITY_HEADER_NAME": self.model.config["identity_header_name"],
            # so that MLflow's auth app can import the charm-written custom authentication module:
            "PYTHONPATH": AUTH_CONFIG_DIR,
        }
        if self.proxy_mode:
            proxy_environment_variables = {
                "AWS_ACCESS_KEY_ID": artifact_store_data["access_key"],
                "AWS_SECRET_ACCESS_KEY": artifact_store_data["secret_key"],
                "MLFLOW_ARTIFACTS_DESTINATION": s3_bucket_uri,
                "MLFLOW_DEFAULT_ARTIFACT_ROOT": "mlflow-artifacts:/",
                "MLFLOW_S3_ENDPOINT_URL": self._extract_s3_endpoint(artifact_store_data),
                "MLFLOW_SERVE_ARTIFACTS": "True",
            }
            if artifact_store_data["tls_ca_chain"]:
                proxy_environment_variables["AWS_CA_BUNDLE"] = S3_CA_BUNDLE_CONTAINER_PATH
            if artifact_store_data["region"]:
                proxy_environment_variables["AWS_DEFAULT_REGION"] = artifact_store_data["region"]
            environment_variables.update(proxy_environment_variables)
        else:
            environment_variables.update(
                {
                    "MLFLOW_DEFAULT_ARTIFACT_ROOT": s3_bucket_uri,
                    "MLFLOW_SERVE_ARTIFACTS": "False",
                }
            )

        return environment_variables

    def _get_or_create_auth_secrets(self) -> dict:
        """Return the shared RBAC credentials, generating and persisting them on first use.

        The Flask secret key and the MLflow super-admin password are generated once by the leader
        and stored in an application-scoped Juju secret, so they stay stable across restarts and
        identical across replicas (the auth app requires a consistent Flask secret key).

        Raises:
            ErrorWithStatus(..., Waiting) if the credentials have not been generated yet (a
            non-leader unit observed before the leader created them), so the caller can defer.
        """
        try:
            content = self.model.get_secret(label=AUTH_SECRET_LABEL).get_content()
        except SecretNotFoundError:
            if not self.unit.is_leader():
                raise ErrorWithStatus(
                    "Waiting for the leader to generate the RBAC credentials", WaitingStatus
                )
            content = {
                "flask-secret-key": secrets.token_urlsafe(32),
                "admin-password": secrets.token_urlsafe(32),
            }
            self.app.add_secret(content, label=AUTH_SECRET_LABEL)

        return {
            "flask_secret_key": content["flask-secret-key"],
            "admin_password": content["admin-password"],
        }

    def _reconcile_auth_config(self) -> None:
        """Render and push the RBAC auth config and the custom authentication module.

        Writes into the workload container the custom authentication module (shipped in the charm)
        and the rendered basic_auth.ini that points MLflow at it, at the paths that the server's
        environment then references via `MLFLOW_AUTH_CONFIG_PATH` and `PYTHONPATH`.
        """
        auth_secrets = self._get_or_create_auth_secrets()

        custom_auth_module = Path(AUTH_MODULE_SOURCE_PATH).read_text()
        self.container.push(AUTH_MODULE_CONTAINER_PATH, custom_auth_module, make_dirs=True)

        auth_config = Template(Path(AUTH_CONFIG_TEMPLATE_PATH).read_text()).render(
            database_uri=self._get_backend_store_uri(),
            admin_username=MLFLOW_SUPER_ADMIN_USERNAME,
            admin_password=auth_secrets["admin_password"],
            authorization_function=AUTHORIZATION_FUNCTION,
        )
        self.container.push(AUTH_CONFIG_CONTAINER_PATH, auth_config, make_dirs=True)

    def _reconcile_policy_resource_manager(self):
        if not self.unit.is_leader():
            return
        if self.model.get_relation(SERVICE_MESH_RELATION_NAME):
            self._policy_resource_manager.reconcile(
                policies=[], mesh_type=self._mesh.mesh_type, raw_policies=[self._allow_all_policy]
            )

    def _remove_authorization_policies(self, _):
        if not self.unit.is_leader():
            return
        self._policy_resource_manager.reconcile(
            policies=[], mesh_type=MeshType.istio, raw_policies=[]
        )

    def _on_upgrade_charm(self, event) -> None:
        """Handle the upgrade-charm event by running migrations to possibly newer database schemas.

        Migrate an out-of-date tracking database schema so that a charm refresh shipping an MLflow
        release with schema changes is applied automatically instead of leaving the workload to
        crash-loop. A distinctive Maintenance status is set while the migration runs.

        The workload is (re)planned and the unit status reconciled by the `config-changed` handler
        (`_on_event`) that Juju always fires right after `upgrade-charm`, so this handler only needs
        to migrate the schema before that and not to replan the workload or restore the status.

        Transient failures (the workload container or the database not yet being reachable) surface
        as a Waiting status and defer the event, so Juju re-emits it on a later hook and the
        migration is retried. A genuine migration failure surfaces as a Blocked status and is not
        deferred, as it needs manual intervention.
        """
        if not self.unit.is_leader():
            self.logger.info("Not a leader, skipping database schema migration.")
            return

        try:
            if not self.container.can_connect():
                raise ErrorWithStatus(
                    "Workload container is not ready; will retry the schema migration.",
                    WaitingStatus,
                )

            self._reconcile_database_schema()

        except ErrorWithStatus as err:
            self.model.unit.status = err.status
            self.logger.info(f"Event {event} stopped early with message: {str(err)}")

            # NOTE: deferring only when waiting, so that a genuine migration failure (Blocked) is
            # surfaced and not retried while the migration is instead retried when waiting for the
            # workload to be ready or for something else:
            if isinstance(err.status, WaitingStatus):
                event.defer()

    def _on_pebble_ready(self, _):
        """Configure started container."""
        if not self.container.can_connect():
            # Pebble Ready event should indicate that container is available
            raise ErrorWithStatus("Pebble is ready and container is not ready", BlockedStatus)

        # proceed with other actions
        self._on_event(_)

    def _on_backend_store_relation_removed(self, _) -> None:
        """Event is fired when relation with the backend store is broken."""
        self.unit.status = BlockedStatus(
            f"Please add the relation {RELATION_ENDPOINT_FOR_BACKEND_STORE_DB}"
        )

    def _send_manifests(
        self, context, manifest_files, relation_requirer: KubernetesManifestRequirerWrapper
    ):
        """Send manifests from folder to desired relation."""
        manifests = self._create_manifests(manifest_files, context)
        relation_requirer.send_data(manifests)

    def _create_manifests(self, manifest_files, context):
        """Create manifests string for given folder and context."""
        manifests = []
        for file in manifest_files:
            template = Template(Path(file).read_text())
            rendered_template = template.render(**context)
            manifest = KubernetesManifest(rendered_template)
            # skipping templates that render to an empty document, such as parametrized resources
            # that are intentionally omitted when in proxy mode, so they are not sent as null:
            if manifest.manifest is None:
                continue
            manifests.append(manifest)
        return manifests

    def _send_ingress_info(self, interfaces):
        if interfaces[INGRESS_MODES_TO_RELATION_NAMES["sidecar"]]:
            interfaces[INGRESS_MODES_TO_RELATION_NAMES["sidecar"]].send_data(
                {
                    "prefix": INGRESS_PATH_MATCHED_PREFIX,
                    "rewrite": INGRESS_PATH_REWRITTEN_PREFIX,
                    "service": self._service_name,
                    "namespace": self._namespace,
                    "port": self._mlflow_port,
                }
            )

    def _on_event(self, event) -> None:
        """Perform all required actions for the Charm."""
        try:
            self._check_leader()

            interfaces = self._get_interfaces()

            self._check_no_conflicting_ingress_relations()

            self._ensure_bucket_exists()

            if not self.container.can_connect():
                raise ErrorWithStatus(
                    f"Container {self._container_name} is not ready", WaitingStatus
                )

            # TODO: remove once this issue is fixed: https://github.com/mlflow/mlflow/issues/19943
            # clearing MySQL's binlog trigger-creation restriction before the workload starts and
            # auto-initializes the database schema on a fresh deployment (an operation that indeed
            # requires the privileges granted with this step):
            self._ensure_trigger_creation_allowed(self._get_backend_store_uri())

            # (re)rendering the required authentication configurations, including the custom
            # authentication module, into the workload:
            self._reconcile_auth_config()

            update_layer(
                self._container_name, self._container, self._mlflow_server_layer, self.logger
            )

            self._reconcile_s3_ca_bundle(self._get_artifact_store_data(interfaces))

            self._reconcile_policy_resource_manager()

            if not self.exporter_container.can_connect():
                raise ErrorWithStatus(
                    f"Container {self._exporter_container_name} is not ready", WaitingStatus
                )
            update_layer(
                self._exporter_container_name,
                self.exporter_container,
                self._mlflow_exporter_layer,
                self.logger,
            )

            self._send_manifests(
                self.secrets_context, SECRETS_FILES, self.secrets_manifests_wrapper
            )
            self._send_manifests(
                self.poddefaults_context, PODDEFAULTS_FILES, self.poddefaults_manifests_wrapper
            )
            self._send_ingress_info(interfaces)

        except ErrorWithStatus as err:
            self.model.unit.status = err.status
            self.logger.info(f"Event {event} stopped early with message: {str(err)}")
            return

        self.model.unit.status = ActiveStatus()

    def _on_ambient_mode_ingress_ready(self, _):
        """Configure the ingess for ambient mode."""
        # submit_config publishes this same config to every istio-ingress-route
        # relation, so all related ingress providers are (re)configured at once.
        if self.unit.is_leader():

            try:
                self.ambient_mode_ingress.submit_config(self._ingress_config)

            except Exception as error:
                error_message = f"Failed to submit ingress config: {error}"
                self.model.unit.status = BlockedStatus(error_message)
                self.logger.error(error_message)
                return

        self.model.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(MlflowCharm)
