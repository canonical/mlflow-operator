#!/usr/bin/env python3
# Copyright 2020 Luke Marsden
# See LICENSE file for licensing details.

import json
import logging
from base64 import b64encode
from datetime import datetime

from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus

from minio import Minio
from minio.error import S3Error
from opslib.mysql import MySQLClient, MySQLRelationEvent

logger = logging.getLogger(__name__)


DB_NAME = "mlflow"
BUCKET_NAME = "mlflow"


class MlflowCharm(CharmBase):
    _state = StoredState()

    def __init__(self, *args):
        logger.info("================================")
        logger.info("__init__ is running")
        logger.info("================================")
        super().__init__(*args)

        self._state.set_default(
            db_available=False,
            db_conn_str=None,
            db_host=None,
            db_port=None,
            db_name=None,
            db_user=None,
            db_password=None,
            db_root_password=None,
            minio_egress_subnets=None,
            minio_ingress_address=None,
            minio_ip=None,
            minio_password=None,
            minio_port=None,
            minio_private_address=None,
            minio_user=None,
            prometheus_port=None,
            prometheus_metrics_path=None,
            prometheus_labels=None,
            prometheus_scrape_interval=None,
            prometheus_scrape_timeout=None,
        )
        self.db = MySQLClient(self, "db")  # 'db' relation in metadata.yaml
        self.framework.observe(self.db.on.database_changed, self._on_database_changed)

        self.framework.observe(self.on.config_changed, self.set_pod_spec)
        self.framework.observe(self.on.install, self.set_pod_spec)
        self.framework.observe(self.on.upgrade_charm, self.set_pod_spec)

        # Register relation events
        self.framework.observe(self.on.db_relation_joined, self._on_db_relation_changed)
        self.framework.observe(
            self.on.db_relation_changed, self._on_db_relation_changed
        )
        self.framework.observe(
            self.on.minio_relation_joined, self._on_minio_relation_changed
        )
        self.framework.observe(
            self.on.minio_relation_changed, self._on_minio_relation_changed
        )
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

    def dequote(self, string):
        """
        If a string has single or double quotes around it, remove them.
        """
        if (string[0] == string[-1]) and string.startswith(("'", '"')):
            return string[1:-1]
        return string

    def create_bucket(self, bucket):
        """
        Create the bucket in minio to store the MLFlow artifacts
        """

        try:
            # Make the bucket if not exist.
            found = self.minio.bucket_exists(bucket)
            if not found:
                self.minio.make_bucket(bucket)
                logger.info("Bucket '{}' created.".format(bucket))
            else:
                logger.info("Bucket '{}' already exists.".format(bucket))
        except S3Error as err:
            logger.error(err)
            return

        # Set the bucket policy
        policy = {
            "Version": datetime.today().strftime("%Y-%m-%d"),
            "Statement": [
                {
                    "Sid": "",
                    "Effect": "Allow",
                    "Principal": {"AWS": "*"},
                    "Action": "s3:GetBucketLocation",
                    "Resource": "arn:aws:s3:::{}".format(bucket),
                },
                {
                    "Sid": "",
                    "Effect": "Allow",
                    "Principal": {"AWS": "*"},
                    "Action": "s3:ListBucket",
                    "Resource": "arn:aws:s3:::{}".format(bucket),
                },
                {
                    "Sid": "",
                    "Effect": "Allow",
                    "Principal": {"AWS": "*"},
                    "Action": "s3:GetObject",
                    "Resource": "arn:aws:s3:::{}/*".format(bucket),
                },
                {
                    "Sid": "",
                    "Effect": "Allow",
                    "Principal": {"AWS": "*"},
                    "Action": "s3:PutObject",
                    "Resource": "arn:aws:s3:::{}/*".format(bucket),
                },
            ],
        }

        self.minio.set_bucket_policy(bucket, json.dumps(policy))

    def _on_prometheus_relation_changed(self, event):
        logger.info("================================")
        logger.info(f"_on_prometheus_relation_changed is running; {event}")
        logger.info("================================")
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
        logger.info("================================")
        logger.info(f"_on_pod_defaults_relation_changed is running; {event}")
        logger.info("================================")

        if not self._state.db_host:
            self.unit.status = WaitingStatus("Waiting for database relation")
            event.defer()
            return

        if not self._state.minio_ingress_address:
            self.unit.status = WaitingStatus("Waiting for minio relation")
            event.defer()
            return

        config = self.model.config

        event.relation.data[self.app]["pod-defaults"] = json.dumps(
            {
                "minio": {
                    "env": {
                        "AWS_ACCESS_KEY_ID": self._state.minio_user,
                        "AWS_SECRET_ACCESS_KEY": self._state.minio_password,
                        "MLFLOW_S3_ENDPOINT_URL": "http://{}:{}".format(
                            self._state.minio_ingress_address, self._state.minio_port
                        ),
                        "MLFLOW_TRACKING_URI": "mlflow:{}".format(
                            config["mlflow_port"]
                        ),
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

    def _on_minio_relation_changed(self, event):
        logger.info("================================")
        logger.info(f"_on_minio_relation_changed is running; {event}")
        logger.info("================================")
        data = event.relation.data[event.unit]
        self._state.minio_egress_subnet = data.get("egress-subnets")
        self._state.minio_ingress_address = data.get("ingress-address")
        self._state.minio_ip = data.get("ip")
        self._state.minio_port = data.get("port")
        self._state.minio_private_address = data.get("private-address")
        self._state.minio_password = self.dequote(data.get("password"))
        self._state.minio_user = self.dequote(data.get("user"))

        if self._state.minio_ingress_address:
            self.minio = Minio(
                "{}:{}".format(
                    self._state.minio_ingress_address, self._state.minio_port
                ),
                access_key=self._state.minio_user,
                secret_key=self._state.minio_password,
                secure=False,
            )

            self.create_bucket(BUCKET_NAME)
            self.set_pod_spec(event)

    def _on_database_changed(self, event: MySQLRelationEvent):
        logger.info("================================")
        logger.info(f"_on_database_changed is running; {event}")
        logger.info("================================")
        self._state.db_available = event.is_available  # Boolean flag
        self._state.db_conn_str = event.connection_string  # host={host} port={port} ...
        self._state.db_host = event.host
        self._state.db_port = event.port
        self._state.db_name = event.database
        self._state.db_user = event.user
        self._state.db_password = event.password
        self._state.db_root_password = event.root_password
        if self._state.db_host:
            self.set_pod_spec(event)

    def _on_db_relation_changed(self, event):
        logger.info("================================")
        logger.info(f"_on_db_relation_changed is running; {event}")
        logger.info("================================")
        data = event.relation.data[event.unit]
        self._state.db_host = data.get("host")
        self._state.db_port = data.get("port")
        self._state.db_user = data.get("user")
        self._state.db_password = data.get("password")
        self._state.db_root_password = data.get("root_password")
        if self._state.db_host:
            self.set_pod_spec(event)

    def set_pod_spec(self, event):
        logger.info("================================")
        logger.info(f"in set_pod_spec; {event}")
        logger.info("================================")

        if not self._state.db_host:
            self.unit.status = WaitingStatus("Waiting for database relation")
            event.defer()
            return

        if not self._state.minio_ingress_address:
            self.unit.status = WaitingStatus("Waiting for minio relation")
            event.defer()
            return

        if not self.model.unit.is_leader():
            logger.info("Not a leader, skipping set_pod_spec")
            self.model.unit.status = ActiveStatus()
            return

        self.model.unit.status = MaintenanceStatus("Setting pod spec")

        config = self.model.config
        self.model.pod.set_spec(
            {
                "version": 3,
                "containers": [
                    {
                        "name": "mlflow",
                        "imageDetails": {"imagePath": "quay.io/helix-ml/mlflow:1.13.1"},
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
                                self._state.db_user,
                                self._state.db_password,
                                self._state.db_host,
                                self._state.db_port,
                                self._state.db_name,
                            ),
                            "--default-artifact-root",
                            "s3://{}/".format(BUCKET_NAME),
                        ],
                        "envConfig": {
                            "MLFLOW_TRACKING_URI": "mysql+pymysql://{}:{}@{}:{}/{}".format(
                                self._state.db_user,
                                self._state.db_password,
                                self._state.db_host,
                                self._state.db_port,
                                self._state.db_name,
                            ),
                            "AWS_ACCESS_KEY_ID": self._state.minio_user,
                            "AWS_SECRET_ACCESS_KEY": self._state.minio_password,
                            "AWS_DEFAULT_REGION": "us-east-1",
                            "MLFLOW_S3_ENDPOINT_URL": "http://{}:{}".format(
                                self._state.minio_ingress_address,
                                self._state.minio_port,
                            ),
                        },
                    }
                ],
                "kubernetesResources": {
                    "secrets": [
                        {
                            "name": "seldon-init-container-secret",
                            "data": {
                                "AWS_ENDPOINT_URL": b64encode(
                                    "http://{}:{}".format(
                                        self._state.minio_ingress_address,
                                        self._state.minio_port,
                                    ).encode("utf-8")
                                ).decode("utf-8"),
                                "AWS_ACCESS_KEY_ID": b64encode(
                                    self._state.minio_user.encode("utf-8")
                                ).decode("utf-8"),
                                "AWS_SECRET_ACCESS_KEY": b64encode(
                                    self._state.minio_password.encode("utf-8")
                                ).decode("utf-8"),
                                "USE_SSL": b64encode("false".encode("utf-8")).decode(
                                    "utf-8"
                                ),
                            },
                        }
                    ],
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
                        {
                            "name": "minio-external",
                            "spec": {
                                "type": "NodePort",
                                "selector": {
                                    "app.kubernetes.io/name": "minio",
                                },
                                "ports": [
                                    {
                                        "protocol": "TCP",
                                        "port": config["minio_port"],
                                        "targetPort": config["minio_port"],
                                        "nodePort": config["minio_nodeport"],
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
    main(MlflowCharm)
