#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
#

import logging

from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from lightkube.models.core_v1 import ServicePort
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import ChangeError, Layer
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed, get_interfaces

from services.s3 import S3BucketWrapper, validate_s3_bucket_name


class MlflowCharm(CharmBase):
    """A Juju Charm for MLFlow."""

    def __init__(self, *args):
        super().__init__(*args)

        self.logger = logging.getLogger(__name__)
        self._port = self.model.config["mlflow_port"]
        self._container_name = "mlflow-server"
        self._container = self.unit.get_container(self._container_name)

        self.framework.observe(self.on.upgrade_charm, self._on_event)
        self.framework.observe(self.on.config_changed, self._on_event)
        self.framework.observe(self.on.mlflow_server_pebble_ready, self._on_pebble_ready)

        for rel in self.model.relations.keys():
            self.framework.observe(self.on[rel].relation_changed, self._on_event)
        self._create_service()

    @property
    def container(self):
        """Return container."""
        return self._container

    def _create_service(self):
        """Create k8s service based on charm'sconfig."""
        if self.config["enable_mlflow_nodeport"]:
            service_type = "NodePort"
            self._node_port = self.model.config["mlflow_nodeport"]
            port = ServicePort(
                int(self._port),
                name=f"{self.app.name}",
                targetPort=int(self._port),
                nodePort=int(self._node_port),
            )
        else:
            service_type = "ClusterIP"
            port = ServicePort(int(self._port), name=f"{self.app.name}")
        self.service_patcher = KubernetesServicePatch(
            self,
            [port],
            service_type=service_type,
            service_name=f"{self.model.app.name}",
            refresh_event=self.on.config_changed,
        )

    @staticmethod
    def _get_env_vars(relational_db_data, object_storage_data):
        """Return environment variables based on model configuration."""

        ret_env_vars = {
            "AWS_ENDPOINT_URL": f"http://{object_storage_data['service']}.{object_storage_data['namespace']}:{object_storage_data['port']}",  # noqa: E501
            "AWS_ACCESS_KEY_ID": object_storage_data["access-key"],
            "AWS_SECRET_ACCESS_KEY": object_storage_data["secret-key"],
            "USE_SSL": str(object_storage_data["secure"]).lower(),
            "DB_ROOT_PASSWORD": relational_db_data["root_password"],
            "MLFLOW_TRACKING_URI": f"mysql+pymysql://root:{relational_db_data['root_password']}@{relational_db_data['host']}:{relational_db_data['port']}/{relational_db_data['database']}",  # noqa: E501
        }
        return ret_env_vars

    def _charmed_mlflow_layer(self, env_vars, default_artifact_root) -> Layer:
        """Create and return Pebble framework layer."""

        layer_config = {
            "summary": "mlflow-server layer",
            "description": "Pebble config layer for mlflow-server",
            "services": {
                self._container_name: {
                    "override": "replace",
                    "summary": "Entrypoint of mlflow-server image",
                    "command": (
                        "mlflow "
                        "server "
                        "--host "
                        "0.0.0.0 "
                        "--port "
                        f"{self._port} "
                        "--backend-store-uri "
                        "$(MLFLOW_TRACKING_URI) "
                        "--default-artifact-root "
                        f"s3://{default_artifact_root}/"
                    ),
                    "startup": "enabled",
                    "environment": env_vars,
                }
            },
        }

        return Layer(layer_config)

    def _get_interfaces(self):
        """Retrieve interface object."""
        try:
            interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            raise ErrorWithStatus(err, WaitingStatus)
        except NoCompatibleVersions as err:
            raise ErrorWithStatus(err, BlockedStatus)
        return interfaces

    def _get_relational_db_data(
        self,
    ):
        relational_db = self.model.relations["relational-db"]
        if len(relational_db) > 1:
            raise ErrorWithStatus(
                f"Too many mysql relations. Found {len(relational_db)}, expected 1", BlockedStatus
            )

        try:
            relational_db = relational_db[0]
            db_unit = list(relational_db.units)[0]
            relational_db = relational_db.data[db_unit]
            relational_db["database"]
            return relational_db
        except IndexError:
            raise ErrorWithStatus("Waiting for relational-db relation data", WaitingStatus)
        except KeyError:
            raise ErrorWithStatus("Missing data in relational-db relation", WaitingStatus)

    def _get_object_storage_data(self, interfaces):
        """Unpacks and returns the object-storage relation data.

        Raises CheckFailedError if an anticipated error occurs.
        """
        if not ((obj_storage := interfaces["object-storage"]) and obj_storage.get_data()):
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

        if (
            not s3_wrapper.check_if_bucket_accessible(bucket_name)
            and not self.config["create_default_artifact_root_if_missing"]
        ):
            raise ErrorWithStatus(
                "Error with default S3 artifact store - bucket not accessible or does not "
                "exist. Set create_default_artifact_root_if_missing=True to automatically "
                "create a missing default bucket",
                BlockedStatus,
            )
        elif (
            not s3_wrapper.check_if_bucket_accessible(bucket_name)
            and self.config["create_default_artifact_root_if_missing"]
        ):
            return False
        return True

    def _check_leader(self):
        """Check if this unit is a leader."""
        if not self.unit.is_leader():
            self.logger.info("Not a leader, skipping setup")
            raise ErrorWithStatus("Waiting for leadership", WaitingStatus)

    def _update_layer(self, envs, default_artifact_root) -> None:
        """Update the Pebble configuration layer (if changed)."""
        current_layer = self.container.get_plan()
        new_layer = self._charmed_mlflow_layer(envs, default_artifact_root)
        if current_layer.services != new_layer.services:
            self.unit.status = MaintenanceStatus("Applying new pebble layer")
            self.container.add_layer(self._container_name, new_layer, combine=True)
            try:
                self.logger.info("Pebble plan updated with new configuration, replaning")
                self.container.replan()
            except ChangeError as err:
                raise ErrorWithStatus(f"Failed to replan with error: {str(err)}", BlockedStatus)

    def _on_pebble_ready(self, _):
        """Configure started container."""
        if not self.container.can_connect():
            # Pebble Ready event should indicate that container is available
            raise ErrorWithStatus("Pebble is ready and container is not ready", BlockedStatus)

        # proceed with other actions
        self._on_event(_)

    def _on_event(self, event) -> None:
        """Perform all required actions for the Charm."""
        try:
            self._check_leader()
            interfaces = self._get_interfaces()
            object_storage_data = self._get_object_storage_data(interfaces)
            relational_db_data = self._get_relational_db_data()
            envs = self._get_env_vars(relational_db_data, object_storage_data)

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
            self._update_layer(envs, bucket_name)
        except ErrorWithStatus as err:
            self.model.unit.status = err.status
            self.logger.info(f"Event {event} stopped early with message: {str(err)}")
            return
        self.model.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(MlflowCharm)