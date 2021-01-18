#!/usr/bin/env python3
# Copyright 2020 Luke Marsden
# See LICENSE file for licensing details.

import logging

from ops.charm import CharmBase
from ops.main import main
from ops.framework import StoredState
from ops.model import ActiveStatus, MaintenanceStatus
logger = logging.getLogger(__name__)

import ops.lib
from opslib.mysql import MySQLClient, MySQLRelationEvent

DB_NAME = "mlflow"

class MlflowCharm(CharmBase):
    _stored = StoredState()

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
        self._state.db_available = event.is_available  # Boolean flag
        self._state.db_conn_str = event.connection_string  # host={host} port={port} ...
        self._state.db_host = event.host
        self._state.db_port = event.port
        self._state.db_name = event.database
        self._state.db_user = event.user
        self._state.db_password = event.password
        self._state.db_root_password = event.root_password

    def set_pod_spec(self, event):
        logger.info("================================")
        logger.info("in set_pod_spec")
        logger.info("================================")
        if not self.model.unit.is_leader():
            logger.info('Not a leader, skipping set_pod_spec')
            self.model.unit.status = ActiveStatus()
            return

        self.model.unit.status = MaintenanceStatus('Setting pod spec')

        self.model.pod.set_spec(
            # TODO: put mysql connection details in here, as env vars for mlflow
            {
                'version': 3,
                'containers': [
                    {
                        'name': 'nginx',
                        'imageDetails': {'imagePath': 'nginx:latest'},
                        'ports': [{'name': 'http', 'containerPort': 80}],
                    }
                ],
            },
        )
        self.model.unit.status = ActiveStatus()

if __name__ == "__main__":
    main(MlflowCharm)
