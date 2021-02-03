#!/usr/bin/env python3
# Copyright 2020 Luke Marsden
# See LICENSE file for licensing details.

import logging

from ops.charm import CharmBase
from ops.main import main
from ops.framework import StoredState
from ops.model import ActiveStatus, MaintenanceStatus
logger = logging.getLogger(__name__)

from opslib.mysql import MySQLClient, MySQLRelationEvent

DB_NAME = "mlflow"

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
        )
        self.db = MySQLClient(self, 'db')  # 'db' relation in metadata.yaml
        self.framework.observe(self.db.on.database_changed, self._on_database_changed)

        self.framework.observe(self.on.install, self.set_pod_spec)
        self.framework.observe(self.on.upgrade_charm, self.set_pod_spec)

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
        self.set_pod_spec(event)

    def set_pod_spec(self, event):
        logger.info("================================")
        logger.info(f"in set_pod_spec; {event}")
        logger.info("================================")
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
                        'args': ['--host', '0.0.0.0'],
                        'envConfig': {'MLFLOW_TRACKING_URI':
                                        'mysql://{}:{}@{}:{}/{}'.format(self._state.db_user,
                                                                      self._state.db_password,
                                                                      self._state.db_host,
                                                                      self._state.db_port,
                                                                      self._state.db_name) \
                                                                      if (self._state.db_user is not None and
                                                                         self._state.db_password is not None and
                                                                         self._state.db_host is not None and
                                                                         self._state.db_port is not None and
                                                                         self._state.db_name is not None) else ""}
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
