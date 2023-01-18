#!/usr/bin/env python3
# Copyright 2020 Luke Marsden
# See LICENSE file for licensing details.

"""Charm for the ML Flow Server.

https://github.com/canonical/mlflow-operator
"""

import json
import logging
from base64 import b64encode

from charms.grafana_k8s.v0.grafana_dashboard import GrafanaDashboardProvider
from charms.prometheus_k8s.v0.prometheus_scrape import MetricsEndpointProvider
from oci_image import OCIImageResource, OCIImageResourceError
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, StatusBase, WaitingStatus
from serialized_data_interface import NoCompatibleVersions, NoVersionsListed, get_interfaces

from services.s3 import S3BucketWrapper, validate_s3_bucket_name

PROMETHEUS_PATH = "/metrics"


class Operator(CharmBase):
    """Charm for the ML Flow Server.

    https://github.com/canonical/mlflow-operator
    """

    def __init__(self, *args):
        super().__init__(*args)

        self.image = OCIImageResource(self, "oci-image")
        self.log = logging.getLogger(__name__)
        self.charm_name = self.model.app.name

        self.prometheus_provider = MetricsEndpointProvider(
            charm=self,
            relation_name="metrics-endpoint",
            jobs=[
                {
                    "metrics_path": PROMETHEUS_PATH,
                    "static_configs": [
                        {
                            "targets": [
                                "{}.{}.svc.cluster.local:{}".format(
                                    self.model.app.name,
                                    self.model.name,
                                    self.config["mlflow_port"],
                                )
                            ]
                        }
                    ],
                }
            ],
        )

        self.dashboard_provider = GrafanaDashboardProvider(self)

        for event in [
            self.on.install,
            self.on.leader_elected,
            self.on.upgrade_charm,
            self.on.config_changed,
            self.on.db_relation_changed,
            self.on["object-storage"].relation_changed,
            self.on["ingress"].relation_changed,
        ]:
            self.framework.observe(event, self.main)

        # Register relation events
        for event in [
            self.on.pod_defaults_relation_joined,
            self.on.pod_defaults_relation_changed,
        ]:
            self.framework.observe(event, self._on_pod_defaults_relation_changed)

    def _on_pod_defaults_relation_changed(self, event):
        try:
            interfaces = self._get_interfaces()
        except CheckFailedError as check_failed:
            self.model.unit.status = check_failed.status
            return

        obj_storage = list(interfaces["object-storage"].get_data().values())[0]
        config = self.model.config
        endpoint = _gen_obj_storage_endpoint_url(obj_storage)
        tracking = f"{self.model.app.name}.{self.model.name}.svc.cluster.local"
        tracking = f"http://{tracking}:{config['mlflow_port']}"
        event.relation.data[self.app]["pod-defaults"] = json.dumps(
            {
                "minio": {
                    "env": {
                        "MLFLOW_S3_ENDPOINT_URL": endpoint,
                        "MLFLOW_TRACKING_URI": tracking,
                    }
                }
            }
        )

    def main(self, event):
        """Main function of the charm.

        Runs at install, update, config change and relation change.
        """
        try:
            self.model.unit.status = MaintenanceStatus("Validating inputs and computing pod spec")

            self._check_leader()
            interfaces = self._get_interfaces()
            image_details = self._check_image_details()

            mysql = self._configure_mysql()
            obj_storage = _get_obj_storage(interfaces)
            secrets = self._define_secrets(obj_storage=obj_storage, mysql=mysql)

            default_artifact_root = self._validate_default_s3_bucket(obj_storage)

            self._configure_mesh(interfaces)
        except CheckFailedError as check_failed:
            self.model.unit.status = check_failed.status
            self.model.unit.message = check_failed.msg
            return

        self.model.unit.status = MaintenanceStatus("Setting pod spec")

        config = self.model.config

        pod_spec_services = self._get_pod_spec_services(config)

        self.model.pod.set_spec(
            {
                "version": 3,
                "containers": [
                    {
                        "name": "mlflow",
                        "imageDetails": image_details,
                        "ports": [{"name": "http", "containerPort": config["mlflow_port"]}],
                        "args": [
                            "--host",
                            "0.0.0.0",
                            "--backend-store-uri",
                            "$(MLFLOW_TRACKING_URI)",
                            "--default-artifact-root",
                            f"s3://{default_artifact_root}/",
                            "--expose-prometheus",
                            "{}".format(PROMETHEUS_PATH),
                        ],
                        "envConfig": {
                            "db-secret": {"secret": {"name": f"{self.charm_name}-db-secret"}},
                            "aws-secret": {"secret": {"name": f"{self.charm_name}-minio-secret"}},
                            "AWS_DEFAULT_REGION": "us-east-1",
                            "MLFLOW_S3_ENDPOINT_URL": _gen_obj_storage_endpoint_url(obj_storage),
                        },
                    }
                ],
                "kubernetesResources": {
                    "secrets": secrets,
                    "services": pod_spec_services,
                },
            },
        )
        self.model.unit.status = ActiveStatus()

    def _get_pod_spec_services(self, config):
        """Returns service list for pod spec based on enabled service flags."""
        pod_spec_services = []
        if self.config["enable_mlflow_nodeport"]:
            pod_spec_services.append(
                {
                    "name": "mlflow-external",
                    "spec": {
                        "type": "NodePort",
                        "selector": {
                            "app.kubernetes.io/name": "mlflow",
                        },
                        "ports": [
                            {
                                "protocol": "TCP",
                                "port": config["mlflow_port"],
                                "targetPort": config["mlflow_port"],
                                "nodePort": config["mlflow_nodeport"],
                            }
                        ],
                    },
                }
            )
        if self.config["enable_kubeflow_nodeport"]:
            pod_spec_services.append(
                {
                    "name": "kubeflow-external",
                    "spec": {
                        "type": "NodePort",
                        "selector": {
                            "app.kubernetes.io/name": "istio-ingressgateway",
                        },
                        "ports": [
                            {
                                "protocol": "TCP",
                                "port": config["kubeflow_port"],
                                "targetPort": config["kubeflow_port"],
                                "nodePort": config["kubeflow_nodeport"],
                            }
                        ],
                    },
                }
            )
        if self.config["enable_kubeflow_loadbalancer"]:
            pod_spec_services.append(
                {
                    "name": "kubeflow-external-lb",
                    "spec": {
                        "type": "LoadBalancer",
                        "selector": {
                            "app.kubernetes.io/name": "istio-ingressgateway",
                        },
                        "ports": [
                            {
                                "protocol": "TCP",
                                "port": config["kubeflow_port"],
                                "targetPort": config["kubeflow_port"],
                            }
                        ],
                    },
                }
            )
        return pod_spec_services

    def _configure_mesh(self, interfaces):
        if interfaces["ingress"]:
            interfaces["ingress"].send_data(
                {
                    "prefix": "/mlflow/",
                    "rewrite": "/",
                    "service": self.model.app.name,
                    "port": self.model.config["mlflow_port"],
                }
            )

    def _configure_mysql(
        self,
    ):
        mysql = self.model.relations["db"]
        if len(mysql) > 1:
            raise CheckFailedError("Too many mysql relations", BlockedStatus)

        try:
            mysql = mysql[0]
            unit = list(mysql.units)[0]
            mysql = mysql.data[unit]
            mysql["database"]
            return mysql
        except (IndexError, KeyError):
            raise CheckFailedError("Waiting for mysql relation data", WaitingStatus)

    def _check_leader(self):
        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            raise CheckFailedError("Waiting for leadership", WaitingStatus)

    def _get_interfaces(self):
        try:
            interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            raise CheckFailedError(err, WaitingStatus)
        except NoCompatibleVersions as err:
            raise CheckFailedError(err, BlockedStatus)
        return interfaces

    def _check_image_details(self):
        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            raise CheckFailedError(f"{e.status.message}", e.status_type)
        return image_details

    def _validate_default_s3_bucket(self, obj_storage):
        """Validates the default S3 store, ensuring bucket is accessible and creating if needed."""
        # Validate the bucket name
        bucket_name = self.config["default_artifact_root"]
        if not validate_s3_bucket_name(bucket_name):
            msg = (
                f"Invalid value for config default_artifact_root '{bucket_name}'"
                f" - value must be a valid S3 bucket name"
            )
            raise CheckFailedError(msg, BlockedStatus)

        # Ensure the bucket exists, creating it if missing and create_root_if_not_exists==True
        s3_wrapper = S3BucketWrapper(
            access_key=obj_storage["access-key"],
            secret_access_key=obj_storage["secret-key"],
            s3_service=f"{obj_storage['service']}.{obj_storage['namespace']}",
            s3_port=obj_storage["port"],
        )

        if s3_wrapper.check_if_bucket_accessible(bucket_name):
            return bucket_name
        else:
            if self.config["create_default_artifact_root_if_missing"]:
                try:
                    s3_wrapper.create_bucket(bucket_name)
                    return bucket_name
                except Exception as e:
                    raise CheckFailedError(
                        "Error with default S3 artifact store - bucket not accessible or "
                        f"cannot be created.  Caught error: '{str(e)}",
                        BlockedStatus,
                    )
            else:
                raise CheckFailedError(
                    "Error with default S3 artifact store - bucket not accessible or does not "
                    "exist. Set create_default_artifact_root_if_missing=True to automatically "
                    "create a missing default bucket",
                    BlockedStatus,
                )

    def _define_secrets(self, obj_storage, mysql):
        """Returns needed secrets in pod_spec.kubernetesResources.secrets format."""
        return [
            {
                "name": f"{self.charm_name}-minio-secret",
                "data": _minio_credentials_dict(obj_storage=obj_storage),
            },
            {
                "name": f"{self.charm_name}-seldon-init-container-s3-credentials",
                "data": _seldon_credentials_dict(obj_storage=obj_storage),
            },
            {"name": f"{self.charm_name}-db-secret", "data": _db_secret_dict(mysql=mysql)},
        ]

    def _gen_obj_storage_endpoint_url(self, obj_storage):
        """Generate object storage endpoint URL.

        URL generated only if 'service' is set, otherwise it returns empty string.
        """
        endpoint_url = ""
        if "service" in obj_storage and len(obj_storage["service"]) > 0:
            endpoint_url = f"http://{obj_storage['service']}"
            if "namespace" in obj_storage and len(obj_storage["namespace"]) > 0:
                endpoint_url += f".{obj_storage['namespace']}"
            if "port" in obj_storage and len(obj_storage["port"]) > 0:
                endpoint_url += f":{obj_storage['port']}"

        return endpoint_url


class CheckFailedError(Exception):
    """Raise this exception if one of the checks in main fails."""

    def __init__(self, msg, status_type=StatusBase):
        super().__init__()

        self.msg = str(msg)
        self.status_type = status_type
        self.status = status_type(self.msg)


def _gen_obj_storage_endpoint_url(obj_storage):
    """Generate object storage endpoint URL."""
    return f"http://{obj_storage['service']}.{obj_storage['namespace']}:{obj_storage['port']}"


def _b64_encode_dict(d):
    """Returns the dict with values being base64 encoded."""
    # Why do we encode and decode in utf-8 first?
    return {k: b64encode(v.encode("utf-8")).decode("utf-8") for k, v in d.items()}


def _minio_credentials_dict(obj_storage):
    """Returns a dict of minio credentials with the values base64 encoded."""
    minio_credentials = {
        "AWS_ENDPOINT_URL": _gen_obj_storage_endpoint_url(obj_storage),
        "AWS_ACCESS_KEY_ID": obj_storage["access-key"],
        "AWS_SECRET_ACCESS_KEY": obj_storage["secret-key"],
        "USE_SSL": str(obj_storage["secure"]).lower(),
    }
    return _b64_encode_dict(minio_credentials)


def _seldon_credentials_dict(obj_storage):
    """Returns a dict of seldon init-container object storage credentials, base64 encoded."""
    credentials = {
        "RCLONE_CONFIG_S3_TYPE": "s3",
        "RCLONE_CONFIG_S3_PROVIDER": "minio",
        "RCLONE_CONFIG_S3_ACCESS_KEY_ID": obj_storage["access-key"],
        "RCLONE_CONFIG_S3_SECRET_ACCESS_KEY": obj_storage["secret-key"],
        "RCLONE_CONFIG_S3_ENDPOINT": _gen_obj_storage_endpoint_url(obj_storage),
        "RCLONE_CONFIG_S3_ENV_AUTH": "false",
    }
    return _b64_encode_dict(credentials)


def _db_secret_dict(mysql):
    """Returns a dict of db-secret credential data, base64 encoded."""
    db_secret = {
        "DB_ROOT_PASSWORD": mysql["root_password"],
        "MLFLOW_TRACKING_URI": f"mysql+pymysql://root:{mysql['root_password']}@{mysql['host']}"
        f":{mysql['port']}/{mysql['database']}",
    }
    return _b64_encode_dict(db_secret)


def _get_obj_storage(interfaces):
    """Unpacks and returns the object-storage relation data.

    Raises CheckFailedError if an anticipated error occurs.
    """
    if not ((obj_storage := interfaces["object-storage"]) and obj_storage.get_data()):
        raise CheckFailedError("Waiting for object-storage relation data", WaitingStatus)

    try:
        obj_storage = list(obj_storage.get_data().values())[0]
    except Exception as e:
        raise CheckFailedError(
            f"Unexpected error unpacking object storage data - data format not "
            f"as expected. Caught exception: '{str(e)}'",
            BlockedStatus,
        )

    return obj_storage


if __name__ == "__main__":
    main(Operator)
