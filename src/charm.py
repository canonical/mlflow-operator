#!/usr/bin/env python3
# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
#

import base64
import logging
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
from ops import ActionEvent, main
from ops.charm import CharmBase
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import Layer
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
# Path inside the workload container where the artifact store's TLS CA bundle is written, so the
# NOTE: under the Pebble user's home directory, writable by the non-root user:
S3_CA_BUNDLE_CONTAINER_PATH = "/var/lib/pebble/default/s3-ca-bundle.pem"


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
                    "command": "mlflow server",
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
            # Base64-encoded S3 CA bundle so clients doing direct (non-proxy) object storage
            # I/O can mount it and trust the store's TLS certificate. Empty when there is none.
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
            # Whether to mount the S3 CA bundle into client pods and point AWS_CA_BUNDLE at it,
            # so their direct (non-proxy) boto3 connections trust the store's TLS certificate.
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

    def _get_relational_db_data(self) -> dict:
        mysql_relation = self.model.get_relation("relational-db")

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

    def _validate_sdi_interface(self, interfaces: dict, relation_name: str, default_return=None):
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
                return

            # Create the bucket if missing
            self.unit.status = MaintenanceStatus(f"Creating bucket {bucket_name}.")
            s3_wrapper.create_bucket(bucket_name)
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

        In proxy mode the tracking server proxies artifact writes to the S3 store itself, so it
        must trust the store's TLS CA. The bundle is written to the path referenced by the
        AWS_CA_BUNDLE environment variable set in `_generate_environment`. Non-proxy mode and
        stores without a CA chain need no bundle.
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
        https://mlflow.org/docs/2.22.1/api_reference/cli.html#mlflow-server
        """
        try:
            interfaces = self._get_interfaces()
            artifact_store_data = self._get_artifact_store_data(interfaces)
            relational_db_data = self._get_relational_db_data()
        except ErrorWithStatus as error:
            self.logger.error("Failed to generate container configuration.")
            raise error

        s3_bucket_uri = f"s3://{self._resolve_bucket_name(artifact_store_data)}"

        environment_variables = {
            "MLFLOW_BACKEND_STORE_URI": f"mysql+pymysql://{relational_db_data['username']}:{relational_db_data['password']}@{relational_db_data['host']}:{relational_db_data['port']}/{self._database_name}",  # noqa: E501
            "MLFLOW_EXPOSE_PROMETHEUS": METRICS_PATH,
            "MLFLOW_HOST": "0.0.0.0",
            "MLFLOW_PORT": self._mlflow_port,
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

            update_layer(
                self._container_name, self._container, self._mlflow_server_layer, self.logger
            )

            if not self.container.can_connect():
                raise ErrorWithStatus(
                    f"Container {self._container_name} is not ready", WaitingStatus
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
