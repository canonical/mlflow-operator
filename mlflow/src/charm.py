#!/usr/bin/env python3
# Copyright 2020 Luke Marsden
# See LICENSE file for licensing details.

import logging

from ops.charm import CharmBase
from ops.main import main
from ops.framework import StoredState
from ops.model import ActiveStatus, MaintenanceStatus, WaitingStatus
logger = logging.getLogger(__name__)

from opslib.mysql import MySQLClient, MySQLRelationEvent

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
            db_available=False, db_conn_str=None, db_host=None, db_port=None, db_name=None,
            db_user=None, db_password=None, db_root_password=None,
            minio_egress_subnets=None, minio_ingress_address=None, minio_ip=None,
            minio_password=None, minio_port=None, minio_private_address=None, minio_user=None,
        )
        self.db = MySQLClient(self, 'db')  # 'db' relation in metadata.yaml
        self.framework.observe(self.db.on.database_changed, self._on_database_changed)

        self.framework.observe(self.on.install, self.set_pod_spec)
        self.framework.observe(self.on.upgrade_charm, self.set_pod_spec)

        # Register relation events
        self.framework.observe(self.on.db_relation_joined, self._on_db_relation_changed)
        self.framework.observe(self.on.db_relation_changed, self._on_db_relation_changed)
        self.framework.observe(self.on.minio_relation_joined, self._on_minio_relation_changed)
        self.framework.observe(self.on.minio_relation_changed, self._on_minio_relation_changed)

    def _on_minio_relation_changed(self, event):
        logger.info("================================")
        logger.info(f"_on_minio_relation_changed is running; {event}")
        logger.info("================================")
        self._state.minio_egress_subnet = event.relation.data[event.unit].get("egress-subnets")
        self._state.minio_ingress_address = event.relation.data[event.unit].get("ingress-address")
        self._state.minio_ip = event.relation.data[event.unit].get("ip")
        self._state.minio_password = event.relation.data[event.unit].get("password")
        self._state.minio_port = event.relation.data[event.unit].get("port")
        self._state.minio_private_address = event.relation.data[event.unit].get("private-address")
        self._state.minio_user = event.relation.data[event.unit].get("user")

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
        self._state.db_host = event.relation.data[event.unit].get("host")
        self._state.db_port = event.relation.data[event.unit].get("port")
        self._state.db_user = event.relation.data[event.unit].get("user")
        self._state.db_password = event.relation.data[event.unit].get("password")
        self._state.db_root_password = event.relation.data[event.unit].get("root_password")
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

        if not self.model.unit.is_leader():
            logger.info('Not a leader, skipping set_pod_spec')
            self.model.unit.status = ActiveStatus()
            return

        self.model.unit.status = MaintenanceStatus('Setting pod spec')

        self.model.pod.set_spec(
            # TODO: put mysql connection details in here, as env vars for
            # mlflow
            # TODO: connect mlflow to minio
            {
                'version': 3,
                'containers': [
                    {
                        'name': 'mlflow',
                        'imageDetails': {'imagePath': 'quay.io/helix-ml/mlflow:1.13.1'},
                        'ports': [{'name': 'http', 'containerPort': 5000}],
                        'args': ['--host', '0.0.0.0',
                                 '--backend-store-uri', 'mysql+pymysql://{}:{}@{}:{}/{}'.format(self._state.db_user,
                                                                      self._state.db_password,
                                                                      self._state.db_host,
                                                                      self._state.db_port,
                                                                      self._state.db_name),
                                 '--default-artifact-root', 's3://{}/'.format(BUCKET_NAME)],
                        'envConfig': {'MLFLOW_TRACKING_URI':
                                        'mysql+pymysql://{}:{}@{}:{}/{}'.format(self._state.db_user,
                                                                      self._state.db_password,
                                                                      self._state.db_host,
                                                                      self._state.db_port,
                                                                      self._state.db_name)}
                    }
                ],
                'kubernetesResources': {
                    # TODO: make nodeport configurable
                    'services': [
                        {
                            'name': 'mlflow-external',
                            'spec': {
                              'type': 'NodePort',
                              'selector': {
                                'app.kubernetes.io/name': 'mlflow',
                              },
                              'ports': [{
                                  'protocol': 'TCP',
                                  'port': 5000,
                                  'targetPort': 5000,
                                  'nodePort': 31380
                              }],
                            },
                        },{
                            'name': 'kubeflow-external',
                            'spec': {
                              'type': 'NodePort',
                              'selector': {
                                'app.kubernetes.io/name': 'kubeflow-dashboard',
                              },
                              'ports': [{
                                  'protocol': 'TCP',
                                  'port': 8082,
                                  'targetPort': 8082,
                                  'nodePort': 30600
                              }],
                            },
                        }
                    ],
                },
            },
        )
        self.model.unit.status = ActiveStatus()

if __name__ == "__main__":
    main(MlflowCharm)
