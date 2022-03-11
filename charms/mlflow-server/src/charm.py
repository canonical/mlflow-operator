#!/usr/bin/env python3
# Copyright 2020 Luke Marsden
# See LICENSE file for licensing details.

"""Charm for the ML Flow Server.

https://github.com/canonical/mlflow-operator
"""

import json
import logging
from base64 import b64encode

from oci_image import OCIImageResource, OCIImageResourceError
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from serialized_data_interface import (
    NoCompatibleVersions,
    NoVersionsListed,
    get_interfaces,
)

DB_NAME = "mlflow"
BUCKET_NAME = "mlflow"


class Operator(CharmBase):
    """Charm for the ML Flow Server.

    https://github.com/canonical/mlflow-operator
    """

    def __init__(self, *args):
        super().__init__(*args)

        self.image = OCIImageResource(self, "oci-image")
        self.log = logging.getLogger(__name__)

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

        obj_storage = interfaces["object-storage"].get_data()
        config = self.model.config
        endpoint = f"http://{obj_storage['service']}:{obj_storage['port']}"
        tracking = f"{self.model.app.name}.{self.model.name}.svc.cluster.local"
        tracking = f"http://{tracking}:{config['mlflow-port']}"
        event.relation.data[self.app]["pod-defaults"] = json.dumps(
            {
                "minio": {
                    "env": {
                        "AWS_ACCESS_KEY_ID": obj_storage["access-key"],
                        "AWS_SECRET_ACCESS_KEY": obj_storage["secret-key"],
                        "MLFLOW_S3_ENDPOINT_URL": endpoint,
                        "MLFLOW_TRACKING_URI": tracking,
                    }
                }
            }
        )

        requirements = []
        try:
            for req in open("files/mlflow_requirements.txt", "r"):
                requirements.append(req.rstrip("\n"))
        except IOError as e:
            print("Error loading mlflow requirements file:", e)

        event.relation.data[self.unit]["requirements"] = str(requirements)

    def main(self, event):
        """Main function of the charm.

        Runs at install, update, config change and relation change.
        """
        try:
            self._check_leader()
            interfaces = self._get_interfaces()
            image_details = self._check_image_details()
        except CheckFailedError as check_failed:
            self.model.unit.status = check_failed.status
            return

        self._configure_mesh(interfaces)
        config = self.model.config
        charm_name = self.model.app.name

        mysql = self.model.relations["db"]
        if len(mysql) > 1:
            self.model.unit.status = BlockedStatus("Too many mysql relations")
            return

        try:
            mysql = mysql[0]
            unit = list(mysql.units)[0]
            mysql = mysql.data[unit]
            mysql["database"]
        except (IndexError, KeyError):
            self.model.unit.status = WaitingStatus("Waiting for mysql relation data")
            return

        if not ((obj_storage := interfaces["object-storage"]) and obj_storage.get_data()):
            self.model.unit.status = WaitingStatus("Waiting for object-storage relation data")
            return

        self.model.unit.status = MaintenanceStatus("Setting pod spec")

        obj_storage = list(obj_storage.get_data().values())[0]
        secrets = [
            {
                "name": f"{charm_name}-minio-secret",
                "data": {
                    k: b64encode(v.encode("utf-8")).decode("utf-8")
                    for k, v in {
                        "AWS_ENDPOINT_URL": "http://{service}:{port}".format(**obj_storage),
                        "AWS_ACCESS_KEY_ID": obj_storage["access-key"],
                        "AWS_SECRET_ACCESS_KEY": obj_storage["secret-key"],
                        "USE_SSL": str(obj_storage["secure"]).lower(),
                    }.items()
                },
            },
            {
                "name": f"{charm_name}-db-secret",
                "data": {
                    k: b64encode(v.encode("utf-8")).decode("utf-8")
                    for k, v in {
                        "DB_ROOT_PASSWORD": mysql["root_password"],
                        "MLFLOW_TRACKING_URI": "mysql+pymysql://{}:{}@{}:{}/{}".format(
                            "root",
                            mysql["root_password"],
                            mysql["host"],
                            mysql["port"],
                            mysql["database"],
                        ),
                    }.items()
                },
            },
        ]

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
                            "s3://{}/".format(BUCKET_NAME),
                        ],
                        "envConfig": {
                            "db-secret": {"secret": {"name": f"{charm_name}-db-secret"}},
                            "aws-secret": {"secret": {"name": f"{charm_name}-minio-secret"}},
                            "AWS_DEFAULT_REGION": "us-east-1",
                            "MLFLOW_S3_ENDPOINT_URL": "http://{service}:{port}".format(
                                **obj_storage
                            ),
                        },
                    }
                ],
                "kubernetesResources": {
                    "secrets": secrets,
                    "services": [
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
                        },
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
                        },
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
                        },
                    ],
                },
            },
        )
        self.model.unit.status = ActiveStatus()

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


class CheckFailedError(Exception):
    """Raise this exception if one of the checks in main fails."""

    def __init__(self, msg, status_type=None):
        super().__init__()

        self.msg = str(msg)
        self.status_type = status_type
        self.status = status_type(self.msg)


if __name__ == "__main__":
    main(Operator)
