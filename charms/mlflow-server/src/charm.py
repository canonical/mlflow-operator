#!/usr/bin/env python3
# Copyright 2020 Luke Marsden
# See LICENSE file for licensing details.

import json
import logging
from base64 import b64encode

from oci_image import OCIImageResource, OCIImageResourceError
from ops.charm import CharmBase
from ops.framework import StoredState
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
    _state = StoredState()

    def __init__(self, *args):
        super().__init__(*args)

        if not self.unit.is_leader():
            # We can't do anything useful when not the leader, so do nothing.
            self.model.unit.status = WaitingStatus("Waiting for leadership")
            return

        self.image = OCIImageResource(self, "oci-image")

        try:
            self.interfaces = get_interfaces(self)
        except NoVersionsListed as err:
            self.model.unit.status = WaitingStatus(str(err))
            return
        except NoCompatibleVersions as err:
            self.model.unit.status = BlockedStatus(str(err))
            return
        else:
            self.model.unit.status = ActiveStatus()

        self.log = logging.getLogger(__name__)

        self._state.set_default(
            prometheus_port=None,
            prometheus_metrics_path=None,
            prometheus_labels=None,
            prometheus_scrape_interval=None,
            prometheus_scrape_timeout=None,
        )

        self.framework.observe(self.on.config_changed, self.set_pod_spec)
        self.framework.observe(self.on.install, self.set_pod_spec)
        self.framework.observe(self.on.upgrade_charm, self.set_pod_spec)

        # Register relation events
        self.framework.observe(self.on.db_relation_changed, self.set_pod_spec)
        self.framework.observe(
            self.on.pod_defaults_relation_joined,
            self._on_pod_defaults_relation_changed,
        )
        self.framework.observe(
            self.on.pod_defaults_relation_changed,
            self._on_pod_defaults_relation_changed,
        )
        self.framework.observe(
            self.on.prometheus_relation_joined, self._on_prometheus_relation_changed
        )
        self.framework.observe(
            self.on.prometheus_relation_changed, self._on_prometheus_relation_changed
        )
        self.framework.observe(
            self.on["object-storage"].relation_changed, self.set_pod_spec
        )
        self.framework.observe(
            self.on["ingress"].relation_changed, self.configure_ingress
        )

    def configure_ingress(self, event):
        if self.interfaces["ingress"]:
            self.interfaces["ingress"].send_data(
                {
                    "prefix": "/mlflow",
                    "rewrite": "/",
                    "service": self.model.app.name,
                    "port": self.model.config["mlflow_port"],
                }
            )

    def _on_prometheus_relation_changed(self, event):
        data = event.relation.data[event.unit]
        self._state.prometheus_port = data.get("port")
        self._state.prometheus_metrics_path = data.get("metrics_path")
        self._state.prometheus_labels = data.get("labels")
        self._state.prometheus_scrape_interval = data.get("scrape_interval")
        self._state.prometheus_scrape_timeout = data.get("scrape_timeout")

        config = self.model.config
        data = event.relation.data[self.unit]
        data["port"] = str(config["mlflow_port"])
        data["metrics_path"] = "/metrics"
        if config["mlflow_scrape_interval"]:
            data["scrape_interval"] = config["mlflow_scrape_interval"]
        if config["mlflow_scrape_timeout"]:
            data["scrape_timeout"] = config["mlflow_scrape_timeout"]

    def _on_pod_defaults_relation_changed(self, event):
        os = self.interfaces["object-storage"].get_data()
        if not os:
            event.defer()
            return

        config = self.model.config
        endpoint = f"http://{os['service']}:{os['port']}"
        tracking = f"{self.model.app.name}.{self.model.name}.svc.cluster.local"
        tracking = f"http://{tracking}:{config['mlflow-port']}"

        event.relation.data[self.app]["pod-defaults"] = json.dumps(
            {
                "minio": {
                    "env": {
                        "AWS_ACCESS_KEY_ID": os["access-key"],
                        "AWS_SECRET_ACCESS_KEY": os["secret-key"],
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

    def set_pod_spec(self, event):
        try:
            image_details = self.image.fetch()
        except OCIImageResourceError as e:
            self.model.unit.status = e.status
            self.log.info(e)
            return

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

        if not ((os := self.interfaces["object-storage"]) and os.get_data()):
            self.model.unit.status = WaitingStatus(
                "Waiting for object-storage relation data"
            )
            return

        self.model.unit.status = MaintenanceStatus("Setting pod spec")

        os = list(os.get_data().values())[0]
        secrets = [
            {
                "name": "seldon-init-container-secret",
                "data": {
                    k: b64encode(v.encode("utf-8")).decode("utf-8")
                    for k, v in {
                        "AWS_ENDPOINT_URL": "http://{service}:{port}".format(**os),
                        "AWS_ACCESS_KEY_ID": os["access-key"],
                        "AWS_SECRET_ACCESS_KEY": os["secret-key"],
                        "USE_SSL": str(os["secure"]).lower(),
                    }.items()
                },
            }
        ]

        config = self.model.config
        self.model.pod.set_spec(
            {
                "version": 3,
                "containers": [
                    {
                        "name": "mlflow",
                        "imageDetails": image_details,
                        "ports": [
                            {"name": "http", "containerPort": config["mlflow_port"]}
                        ],
                        "args": [
                            "--host",
                            "0.0.0.0",
                            "--expose-prometheus",
                            "/metrics",
                            "--backend-store-uri",
                            "mysql+pymysql://{}:{}@{}:{}/{}".format(
                                "root",
                                mysql["root_password"],
                                mysql["host"],
                                mysql["port"],
                                mysql["database"],
                            ),
                            "--default-artifact-root",
                            "s3://{}/".format(BUCKET_NAME),
                        ],
                        "envConfig": {
                            "MLFLOW_TRACKING_URI": "mysql+pymysql://{}:{}@{}:{}/{}".format(
                                "root",
                                mysql["root_password"],
                                mysql["host"],
                                mysql["port"],
                                mysql["database"],
                            ),
                            "AWS_ACCESS_KEY_ID": os["access-key"],
                            "AWS_SECRET_ACCESS_KEY": os["secret-key"],
                            "AWS_DEFAULT_REGION": "us-east-1",
                            "MLFLOW_S3_ENDPOINT_URL": "http://{service}:{port}".format(
                                **os
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


if __name__ == "__main__":
    main(Operator)
