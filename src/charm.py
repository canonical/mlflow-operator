#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
#

import logging
from pathlib import Path

import botocore.exceptions
from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
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
from ops import main
from ops.charm import CharmBase
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
<<<<<<< HEAD
from ops.pebble import ChangeError, Layer
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed, get_interfaces
=======
from ops.pebble import ExecError, Layer
>>>>>>> 3fa57e8 (refactor: remove sidecar-mode Istio (#515))

from services.s3 import S3BucketWrapper, validate_s3_bucket_name

INGRESS_RELATION_NAME = "istio-ingress-route"
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
    "src/secrets/mlflow-seldon-rclone-secret.j2",
]
SERVICE_MESH_RELATION_NAME = "service-mesh"
<<<<<<< HEAD
=======
SERVICE_MESH_RELATION_REQUIRED_MESSAGE = (
    f"Missing {SERVICE_MESH_RELATION_NAME} relation. "
    f"Please relate to `{SERVICE_MESH_RELATION_NAME}`."
)
SERVICE_MESH_WAYPOINT_PRINCIPAL_REQUIRED_STATUS_MESSAGE = "Missing istio_waypoint_principal config"
SERVICE_MESH_WAYPOINT_PRINCIPAL_REQUIRED_LOG_MESSAGE = (
    "The service-mesh relation is present but config 'istio_waypoint_principal' is not set. "
    "Set it to the SPIFFE principal of the platform namespace's waypoint proxy."
)
TRAFFIC_EXTENSION_TEMPLATE_PATH = "src/service_mesh/traffic-extension.yaml.j2"
TrafficExtension = create_namespaced_resource(
    "extensions.istio.io",
    "v1alpha1",
    "TrafficExtension",
    "trafficextensions",
)
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

# directory inside the workload container where the charm writes the RBAC/auth files it owns: the
# rendered basic_auth.ini and the custom authentication module's files - NOTE: under Pebble's home
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
S3_RELATION_NAME = "s3-credentials"

RELATION_ENDPOINT_FOR_BACKEND_STORE_DB = "relational-db"

MLFLOW_CLIENT_RELATION_ENDPOINT = "mlflow-client"
MLFLOW_CLIENT_DEFAULT_TIER = "edit"
# `resource_type` values an mlflow-client requirer can set on each `entity-permissions` entry:
MLFLOW_CLIENT_WORKSPACE_RESOURCE_TYPE = "workspace"  # workspace-wide grant type
MLFLOW_CLIENT_SUPER_ADMIN_RESOURCE_TYPE = "super-admin"  # cross-workspace, super-admin grant type
# the access tiers accepted on a workspace grant (kept in sync with the reconcile script's TIERS):
MLFLOW_CLIENT_TIERS = frozenset({"read-only", "member", "edit", "admin"})

# the RBAC reconcile script the charm runs in the tracking server's container for any mlflow-client
# requirers over the whole desired set of user grants across workspaces, passed as a JSON argument:
MLFLOW_CLIENT_RECONCILE_SOURCE_PATH = "src/mlflow_client_reconcile.py"


# Normalized artifact store data returned by MlflowCharm._get_artifact_store_data
#  for the `s3` interface
class ArtifactStoreData(TypedDict):
    access_key: str
    secret_key: str
    host: str
    port: int
    secure: bool
    region: str
    bucket: str
    tls_ca_chain: Optional[List[str]]
>>>>>>> 3fa57e8 (refactor: remove sidecar-mode Istio (#515))


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
        self._database_name = "mlflow"
        self._container = self.unit.get_container(self._container_name)
        self._exporter_container = self.unit.get_container(self._exporter_container_name)
        self.database = DatabaseRequires(
            self, relation_name="relational-db", database_name=self._database_name
        )

        self._secrets_manifests_wrapper = None
        self._poddefaults_manifests_wrapper = None

        self.framework.observe(self.on.upgrade_charm, self._on_event)
        self.framework.observe(self.on.config_changed, self._on_event)
        self.framework.observe(self.on.mlflow_server_pebble_ready, self._on_pebble_ready)

        for rel in self.model.relations.keys():
            self.framework.observe(self.on[rel].relation_changed, self._on_event)
        self._create_service()

        self.framework.observe(self.on.update_status, self._on_event)
        self.framework.observe(self.database.on.database_created, self._on_event)
        self.framework.observe(self.database.on.endpoints_changed, self._on_event)
        self.framework.observe(
            self.on.relational_db_relation_broken, self._on_database_relation_removed
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
            self, relation_name=INGRESS_RELATION_NAME
        )

        self.framework.observe(
            self.ambient_mode_ingress.on.ready, self._on_ambient_mode_ingress_ready
        )

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
<<<<<<< HEAD
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
=======
        """Create k8s service based on charm's config."""
        tracking_server_port_definition = ServicePort(
            self._tracking_server_port,
            name=f"{self.app.name}",
        )
        metrics_exporter_port_definition = ServicePort(
            self._exporter_port,
            name=f"{self.app.name}-prometheus-exporter",
        )
>>>>>>> 3fa57e8 (refactor: remove sidecar-mode Istio (#515))

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

    def _get_mlflow_serve_env_vars(self, relational_db_data, bucket_name):
        """Return environment variables for the `mlflow server` command.

        See here how such environment variables provide defaults for `mlflow server` CLI options:
        https://mlflow.org/docs/2.22.1/api_reference/cli.html#mlflow-server
        """
        return {
            "MLFLOW_BACKEND_STORE_URI": f"mysql+pymysql://{relational_db_data['username']}:{relational_db_data['password']}@{relational_db_data['host']}:{relational_db_data['port']}/{self._database_name}",  # noqa: E501
            "MLFLOW_DEFAULT_ARTIFACT_ROOT": f"s3://{bucket_name}",
            "MLFLOW_EXPOSE_PROMETHEUS": METRICS_PATH,
            "MLFLOW_HOST": "0.0.0.0",
            "MLFLOW_PORT": self._mlflow_port,
            "MLFLOW_SERVE_ARTIFACTS": "False",
        }

    def _charmed_mlflow_layer(self, env_vars) -> Layer:
        """Create and return Pebble framework layer."""

        layer_config = {
            "summary": "mlflow-server layer",
            "description": "Pebble config layer for mlflow-server",
            "services": {
                self._container_name: {
                    "override": "replace",
                    "summary": "Entrypoint of mlflow-server image",
                    "command": "mlflow server",
                    "startup": "enabled",
                    "environment": env_vars,  # defaults for `mlflow server` CLI option passed here
                }
            },
        }

        return Layer(layer_config)

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

<<<<<<< HEAD
    def _get_interfaces(self):
        """Retrieve interface object."""
        try:
            interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            raise ErrorWithStatus(err, WaitingStatus)
        except NoCompatibleVersions as err:
            raise ErrorWithStatus(err, BlockedStatus)
        return interfaces

    def _get_relational_db_data(self) -> dict:
        mysql_relation = self.model.get_relation("relational-db")
=======
    @property
    def secrets_context(self) -> dict:
        try:
            artifact_store_data = self._get_artifact_store_data()
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
            artifact_store_data = self._get_artifact_store_data()
        except ErrorWithStatus as error:
            self.logger.error("Failed to generate container configuration.")
            raise error
        poddefaults_context = {
            "app_name": self.app.name,
            "s3_endpoint": self._extract_s3_endpoint(artifact_store_data),
            "mlflow_endpoint": (
                f"http://{self.app.name}.{self._namespace}.svc.cluster.local:"
                f"{self._tracking_server_port}"
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

    def _get_backend_store_db_data(self) -> dict:
        db_relation = self.model.get_relation(RELATION_ENDPOINT_FOR_BACKEND_STORE_DB)
>>>>>>> 3fa57e8 (refactor: remove sidecar-mode Istio (#515))

        # Raise exception and stop execution if the relational-db relation is not established
        if not mysql_relation:
            raise ErrorWithStatus("Please add relation to the database", BlockedStatus)

        data = self.database.fetch_relation_data()
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
                    "Incorrect data found in relation relational-db", WaitingStatus
                )
            return db_data
        raise ErrorWithStatus("Waiting for relational-db relation data", WaitingStatus)

    def _get_object_storage_data(self, interfaces):
        """Unpacks and returns the object-storage relation data.

        Raises CheckFailedError if an anticipated error occurs.
        """
        if not (
            (obj_storage := interfaces[OBJECT_STORAGE_RELATION_NAME]) and obj_storage.get_data()
        ):
            raise ErrorWithStatus("Waiting for object-storage relation data", WaitingStatus)

        try:
            obj_storage = list(obj_storage.get_data().values())[0]
        except Exception as e:
            raise ErrorWithStatus(
                f"Unexpected error unpacking object storage data - data format not "
                f"as expected. Caught exception: '{str(e)}'",
                BlockedStatus,
            )

        return obj_storage

    def _on_get_minio_credentials(self, event):
        """Returns the credentials for minio as an action response."""
        try:
            interfaces = self._get_interfaces()
            object_storage_data = self._get_object_storage_data(interfaces)
            event.set_results(
                {
                    "access-key": object_storage_data["access-key"],
                    "secret-access-key": object_storage_data["secret-key"],
                }
            )
        except ErrorWithStatus:
            event.fail("Minio is not reachable yet. Please try again in a few minutes.")

    def _create_default_s3_bucket(self, s3_wrapper: S3BucketWrapper, bucket_name: str) -> None:
        """Creates an s3 bucket using the default_artifact_root config value.
        Raises:
        ErrorWithStatus: ...
        """
        try:
            s3_wrapper.create_bucket(bucket_name)
        except Exception as e:
            raise ErrorWithStatus(
                "Error with default S3 artifact store - bucket not accessible or "
                f"cannot be created.  Caught error: '{str(e)}",
                BlockedStatus,
            )

    def _validate_default_s3_bucket_name_and_access(
        self, bucket_name: str, s3_wrapper: S3BucketWrapper
    ) -> bool:
        """Validates the default s3 bucket name is valid and the bucket is accessible.
        If it is not accessible and the `create_default_artifact_root_if_missing` config value
        is True, returns False; True otherwise.

        Args:
        bucket_name: ...
        s3_wrapper: ...
        Raises:
        ErrorWithStatus ...

        """
        if not validate_s3_bucket_name(bucket_name):
            msg = (
                f"Invalid value for config default_artifact_root '{bucket_name}'"
                f" - value must be a valid S3 bucket name"
            )
            raise ErrorWithStatus(msg, BlockedStatus)

        try:
            is_bucket_accessible = s3_wrapper.check_if_bucket_accessible(bucket_name)
        except botocore.exceptions.EndpointConnectionError:
            raise ErrorWithStatus("Waiting for object-storage. Can't connect.", WaitingStatus)

        if not is_bucket_accessible and not self.config["create_default_artifact_root_if_missing"]:
            raise ErrorWithStatus(
                "Error with default S3 artifact store - bucket not accessible or does not "
                "exist. Set create_default_artifact_root_if_missing=True to automatically "
                "create a missing default bucket",
                BlockedStatus,
            )
        elif not is_bucket_accessible and self.config["create_default_artifact_root_if_missing"]:
            return False
        return True

    def _check_leader(self):
        """Check if this unit is a leader."""
        if not self.unit.is_leader():
            self.logger.info("Not a leader, skipping setup")
            raise ErrorWithStatus("Waiting for leadership", WaitingStatus)

    def _check_service_mesh_configured(self) -> None:
        """Block when service-mesh relation is missing or when service mesh is enabled without a
        tracking-server waypoint principal."""
        if not self.model.get_relation(SERVICE_MESH_RELATION_NAME):
            self.logger.error(SERVICE_MESH_RELATION_REQUIRED_MESSAGE)
            raise ErrorWithStatus(
                SERVICE_MESH_RELATION_REQUIRED_MESSAGE,
                BlockedStatus,
            )

<<<<<<< HEAD
=======
        if not self._get_tracking_server_waypoint_principal():
            self.logger.error(SERVICE_MESH_WAYPOINT_PRINCIPAL_REQUIRED_LOG_MESSAGE)
            raise ErrorWithStatus(
                SERVICE_MESH_WAYPOINT_PRINCIPAL_REQUIRED_STATUS_MESSAGE,
                BlockedStatus,
            )

    def _generate_environment(self) -> dict:
        """Return environment variables for the `mlflow server` command.

        See here how such environment variables provide defaults for `mlflow server` CLI options:
        https://mlflow.org/docs/3.15.1/api_reference/cli.html#mlflow-server
        """
        try:
            artifact_store_data = self._get_artifact_store_data()
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
            "MLFLOW_PORT": self._tracking_server_port,
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
            # identity aliases (JSON) read by the tracking server's custom authentication logic,
            # kept in the layer environment so that a config change replans and restarts the server
            # to apply the updated aliases:
            "IDENTITY_ALIASES": json.dumps(self._get_identity_aliases()),
            # so that MLflow's auth app can import the charm-written custom authentication module:
            "PYTHONPATH": AUTH_CONFIG_DIR,
            # disabling MLflow's GenAI job-execution subsystem (online scoring, trace archival,
            # prompt optimization): a tracking server does not need it, and it otherwise spawns
            # several extra worker processes that each open database connections:
            "MLFLOW_SERVER_ENABLE_JOB_EXECUTION": "false",
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

    def _get_identity_aliases(self) -> dict[str, str]:
        """Return the validated flat ``{secondary-identity: primary-identity}`` alias map.

        Parsed from the optional ``identity_aliases`` config: each key is a secondary identity
        received via the trusted user-ID header (an IAM email, a programmatic client ID or an
        in-mesh namespace name) and each value is the primary identity already used as an MLflow
        username, so that identities as keys alias identities as respective values. Unmapped
        identities resolve to themselves, and several secondaries may map to one primary identity.

        Raises:
            ErrorWithStatus(..., Blocked) when the config is malformed, so the operator can fix it.
        """
        raw_config = self.model.config["identity_aliases"]
        if not raw_config.strip():
            return {}  # no identity aliases configured

        try:
            aliases = yaml.safe_load(raw_config)
        except yaml.YAMLError as error:
            self.logger.error(f"Config 'identity_aliases' is not valid YAML: {error}")
            raise ErrorWithStatus(
                "Config 'identity_aliases' is not valid YAML. Check the unit logs and act "
                "accordingly.",
                BlockedStatus,
            )

        if aliases is None:
            return {}  # no identity aliases configured

        if not isinstance(aliases, dict):
            self.logger.error(
                "Config 'identity_aliases' must be a YAML mapping of "
                "'<secondary-identity>: <primary-identity>' entries."
            )
            raise ErrorWithStatus(
                "Config 'identity_aliases' is not a valid mapping. Check the unit logs and act "
                "accordingly.",
                BlockedStatus,
            )

        for identity in (*aliases, *aliases.values()):
            if not isinstance(identity, str) or not identity.strip():
                self.logger.error(
                    "Config 'identity_aliases' identities must be non-empty strings; quote any "
                    "value that YAML would otherwise read as a number or boolean."
                )
                raise ErrorWithStatus(
                    "Config 'identity_aliases' has invalid identities. Check the unit logs and "
                    "act accordingly.",
                    BlockedStatus,
                )

        # NOTE: keeping the map flat (single-hop resolution): an identity used as both an alias key
        # and a primary value would chain, so it is rejected rather than resolved ambiguously:
        chained_identities = set(aliases) & set(aliases.values())
        if chained_identities:
            self.logger.error(
                f"Config 'identity_aliases' uses {sorted(chained_identities)} as both an alias "
                "and a primary identity; aliases must map directly to a primary identity."
            )
            raise ErrorWithStatus(
                "Config 'identity_aliases' has a chained alias. Check the unit logs and act "
                "accordingly.",
                BlockedStatus,
            )

        return aliases

    def _reconcile_auth_config(self) -> None:
        """Render and push the RBAC auth config and the custom authentication module.

        Writes into the workload container the custom authentication module (shipped in the charm)
        and the rendered basic_auth.ini that points MLflow at it, at the paths that the server's
        environment then references via `MLFLOW_AUTH_CONFIG_PATH` and `PYTHONPATH`.
        """
        auth_secrets = self._get_or_create_auth_secrets()

        # the Python module defining the custom authentication logic run by the tracking server:
        custom_auth_module = Path(AUTH_MODULE_SOURCE_PATH).read_text()
        self.container.push(AUTH_MODULE_CONTAINER_PATH, custom_auth_module, make_dirs=True)

        # the file configuring the authentication logic of MLflow, which internally points to the
        # above-mentioned custom authentication module's path:
        auth_config = Template(Path(AUTH_CONFIG_TEMPLATE_PATH).read_text()).render(
            database_uri=self._get_backend_store_uri(),
            admin_username=MLFLOW_SUPER_ADMIN_USERNAME,
            admin_password=auth_secrets["admin_password"],
            authorization_function=AUTHORIZATION_FUNCTION,
        )
        self.container.push(AUTH_CONFIG_CONTAINER_PATH, auth_config, make_dirs=True)

    def _reconcile_mlflow_client_grants(self, exclude_relation_id: Optional[int] = None):
        """Reconciles user grants across workspaces as requested over every mlflow-client relation.

        Args:
            exclude_relation_id: relation to skip, set when reconciling on its removal so that the
                departing requirer's grants are pruned.
        """
        if not self.unit.is_leader():
            return

        users_grants_across_workspaces = []
        relation_responses_to_requirers = []
        for relation in self.model.relations[MLFLOW_CLIENT_RELATION_ENDPOINT]:
            if relation.id == exclude_relation_id:
                continue

            for request in self.mlflow_client_provider.requests(relation):
                user_grants = self._build_mlflow_client_user_grants(request)
                if user_grants is None:
                    continue

                users_grants_across_workspaces.append(user_grants)
                relation_responses_to_requirers.append(
                    (
                        relation.id,
                        ResourceProviderModel(
                            request_id=request.request_id,
                            # NOTE: `entity_name` necessary for the library to work properly even
                            # if the requirer is already aware of its value because it imposes it:
                            entity_name=request.entity_name,
                        ),
                    )
                )

        # NOTE: an empty reconcile is skipped only as long as a relation removal is not being
        # processed, so that transient empty requests do not prune grants that are still in use:
        if users_grants_across_workspaces or exclude_relation_id is not None:
            # reconciling user grants across workspaces:
            self._exec_mlflow_client_reconcile(users_grants_across_workspaces)

        # communicating reconcile results to requirers:
        for relation_id, response in relation_responses_to_requirers:
            self.mlflow_client_provider.set_response(relation_id, response)

    def _build_mlflow_client_user_grants(self, request) -> Optional[dict]:
        """Turn an mlflow-client request into the workspace grants of the user it represents.

        Parses the request's `entity-permissions` into:
        - either a set of workspace-wide grants (`resource_type == "workspace"`, one tier each)
        - or a single global super-admin promotion (`resource_type == "super-admin"`)

        Requests are skipped (and logged) when they:
        - either are empty
        - or claim the charm's own, reserved super-admin username
        - or mix super-admin with workspace-wide grants
        - or carry an unknown resource type or tier
        """
        username = request.entity_name
        if not username:
            self.logger.warning("Ignoring mlflow-client request with empty username.")
            return None

        if username == MLFLOW_SUPER_ADMIN_USERNAME:
            self.logger.warning(
                "Ignoring mlflow-client request claiming the charm's reserved super-admin username."
            )
            return None

        is_super_admin = False
        workspace_grants = []
        for permission in request.entity_permissions or []:
            # for a super-admin permission instance:
            if permission.resource_type == MLFLOW_CLIENT_SUPER_ADMIN_RESOURCE_TYPE:
                is_super_admin = True
            # for a workspace-wide permission instance:
            elif permission.resource_type == MLFLOW_CLIENT_WORKSPACE_RESOURCE_TYPE:
                tier = (
                    permission.privileges[0]
                    if permission.privileges
                    else MLFLOW_CLIENT_DEFAULT_TIER
                )
                if tier not in MLFLOW_CLIENT_TIERS:
                    self.logger.error(
                        f"Ignoring mlflow-client request with unknown tier '{tier}'."
                    )
                    return None

                workspace_grants.append([permission.resource_name, tier])
            # for an unknown permission instance:
            else:
                self.logger.error(
                    "Ignoring mlflow-client request with unknown resource type "
                    f"'{permission.resource_type}'."
                )
                return None

        if is_super_admin and workspace_grants:
            self.logger.error(
                "Ignoring mlflow-client request combining super-admin with workspace grants."
            )
            return None

        if not is_super_admin and not workspace_grants:
            self.logger.error(
                "Ignoring mlflow-client request with neither super-admin nor workspace-wide grants."
            )
            return None

        return {
            "username": username,
            "is_super_admin": is_super_admin,
            "workspace_grants": workspace_grants,
        }

    def _exec_mlflow_client_reconcile(self, users_grants_across_workspaces: list) -> None:
        """Reconcile users' grants across workspaces in the tracking server's container.

        Run a Python script in the tracking server's container to reconcile users' grants across
        workspaces as per the given, desired set of grants. The script acts directly on MLflow's
        auth store instead of calling the tracking server's HTTP API, so that the reconcile never
        has to authenticate to MLflow and keeps working even if the charm's super-admin was changed
        or deleted (intentionally or inadvertently) by an external super-admin.
        """
        payload = json.dumps(
            {
                "users": users_grants_across_workspaces,
                "protected_admin": MLFLOW_SUPER_ADMIN_USERNAME,
            }
        )
        reconcile_script = Path(MLFLOW_CLIENT_RECONCILE_SOURCE_PATH).read_text()
        process = self.container.exec(
            ["python3", "-c", reconcile_script, payload],
            service_context=self._container_name,
        )
        try:
            process.wait_output()
        except ExecError as error:
            self.logger.error(f"Failed to reconcile mlflow-client access: {error.stderr}")
            raise ErrorWithStatus(
                "Failed to reconcile mlflow-client access; will retry.", WaitingStatus
            )

    def _on_mlflow_client_relation_broken(self, event) -> None:
        """Revoke a departing mlflow-client requirer's access on relation removal.

        Only grants themselves are revoked, while the requested MLflow user and workspaces are
        retained, as other requirers or externally authenticated users may rely on them. For the
        same reason, grants not directly owned (generated) by the charm such as those externally,
        independently generated by admins and super-admins from the client side are also retained.
        """
        if not self.unit.is_leader():
            return

        if not self.container.can_connect():
            event.defer()
            return

        try:
            # reconciling with the departing relation excluded, so as to exclude its and only its
            # grants (and not the ones of other mlflow-client relations):
            self._reconcile_mlflow_client_grants(exclude_relation_id=event.relation.id)
        except ErrorWithStatus as err:
            self.model.unit.status = err.status
            self.logger.info(f"Event {event} stopped early with message: {str(err)}")
            if isinstance(err.status, WaitingStatus):
                event.defer()

>>>>>>> 3fa57e8 (refactor: remove sidecar-mode Istio (#515))
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

    def _update_layer(self, container, container_name, new_layer) -> None:
        current_layer = self.container.get_plan()
        if current_layer.services != new_layer.services:
            self.unit.status = MaintenanceStatus("Applying new pebble layer")
            container.add_layer(container_name, new_layer, combine=True)
            try:
                self.logger.info(
                    f"Pebble plan updated with new configuration, replaning for {container_name}"
                )
                container.replan()
            except ChangeError as err:
                raise ErrorWithStatus(f"Failed to replan with error: {str(err)}", BlockedStatus)

    def _on_pebble_ready(self, _):
        """Configure started container."""
        if not self.container.can_connect():
            # Pebble Ready event should indicate that container is available
            raise ErrorWithStatus("Pebble is ready and container is not ready", BlockedStatus)

        # proceed with other actions
        self._on_event(_)

    def _on_database_relation_removed(self, _) -> None:
        """Event is fired when relation with postgres is broken."""
        self.unit.status = BlockedStatus("Please add relation to the database")

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
            manifests.append(manifest)
        return manifests

<<<<<<< HEAD
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

=======
>>>>>>> 3fa57e8 (refactor: remove sidecar-mode Istio (#515))
    def _on_event(self, event) -> None:
        """Perform all required actions for the Charm."""
        try:
            self._check_leader()

<<<<<<< HEAD
            interfaces = self._get_interfaces()
            object_storage_data = self._get_object_storage_data(interfaces)
            relational_db_data = self._get_relational_db_data()

            self._check_no_conflicting_ingress_relations()

            s3_wrapper = S3BucketWrapper(
                access_key=object_storage_data["access-key"],
                secret_access_key=object_storage_data["secret-key"],
                s3_service=f"{object_storage_data['service']}.{object_storage_data['namespace']}",
                s3_port=object_storage_data["port"],
            )
            bucket_name = self.config["default_artifact_root"]
            if not self._validate_default_s3_bucket_name_and_access(
                bucket_name=bucket_name, s3_wrapper=s3_wrapper
            ):
                self._create_default_s3_bucket(s3_wrapper, bucket_name)
=======
            self._check_service_mesh_configured()
>>>>>>> 3fa57e8 (refactor: remove sidecar-mode Istio (#515))

            mlflow_serve_envs = self._get_mlflow_serve_env_vars(relational_db_data, bucket_name)

            if not self.container.can_connect():
                raise ErrorWithStatus(
                    f"Container {self._container_name} is not ready", WaitingStatus
                )
            self._reconcile_policy_resource_manager()
            self._update_layer(
                self.container, self._container_name, self._charmed_mlflow_layer(mlflow_serve_envs)
            )
            if not self.exporter_container.can_connect():
                raise ErrorWithStatus(
                    f"Container {self._exporter_container_name} is not ready", WaitingStatus
                )
            self._update_layer(
                self.exporter_container,
                self._exporter_container_name,
                self._mlflow_exporter_layer(),
            )

            secrets_context = {
                "app_name": self.app.name,
                "s3_endpoint": (
                    f"http://{object_storage_data['service']}.{object_storage_data['namespace']}:"
                    f"{object_storage_data['port']}"
                ),
                "s3_type": "s3",
                "s3_provider": "minio",
                "enable_env_auth": "false",
                "access_key": object_storage_data["access-key"],
                "secret_access_key": object_storage_data["secret-key"],
            }
            poddefaults_context = {
                "app_name": self.app.name,
                "s3_endpoint": secrets_context["s3_endpoint"],
                "mlflow_endpoint": (
                    f"http://{self.app.name}.{self._namespace}.svc.cluster.local:"
                    f"{self._mlflow_port}"
                ),
            }
            self._send_manifests(secrets_context, SECRETS_FILES, self.secrets_manifests_wrapper)
            self._send_manifests(
                poddefaults_context, PODDEFAULTS_FILES, self.poddefaults_manifests_wrapper
            )

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
