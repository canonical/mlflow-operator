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
pgsql = ops.lib.use("pgsql", 1, "postgresql-charmers@lists.launchpad.net")

DB_NAME = "mlflow"

class MlflowCharm(CharmBase):
    _stored = StoredState()

    def __init__(self, *args):
        logger.info("================================")
        logger.info("__init__ is running")
        logger.info("================================")
        super().__init__(*args)

        self._stored.set_default(db_conn_str=None, db_uri=None, db_ro_uris=[])
        self.db = pgsql.PostgreSQLClient(self, 'db')  # 'db' relation in metadata.yaml
        self.framework.observe(self.db.on.database_relation_joined, self._on_database_relation_joined)
        self.framework.observe(self.db.on.master_changed, self._on_master_changed)
        self.framework.observe(self.db.on.standby_changed, self._on_standby_changed)

        self.framework.observe(self.on.install, self.set_pod_spec)
        self.framework.observe(self.on.upgrade_charm, self.set_pod_spec)

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

    def _on_database_relation_joined(self, event: pgsql.DatabaseRelationJoinedEvent):
        if self.model.unit.is_leader():
            # Provide requirements to the PostgreSQL server.
            event.database = DB_NAME  # Request database named mydbname
            event.extensions = ['citext']  # Request the citext extension installed
        elif event.database != DB_NAME:
            # Leader has not yet set requirements. Defer, incase this unit
            # becomes leader and needs to perform that operation.
            event.defer()
            return

    def _on_master_changed(self, event: pgsql.MasterChangedEvent):
        if event.database != DB_NAME:
            # Leader has not yet set requirements. Wait until next event,
            # or risk connecting to an incorrect database.
            return
        
        # The connection to the primary database has been created,
        # changed or removed. More specific events are available, but
        # most charms will find it easier to just handle the Changed
        # events. event.master is None if the master database is not
        # available, or a pgsql.ConnectionString instance.
        self._stored.db_conn_str = None if event.master is None else event.master.conn_str
        self._stored.db_uri = None if event.master is None else event.master.uri

        # You probably want to emit an event here or call a setup routine to
        # do something useful with the libpq connection string or URI now they
        # are available.

    def _on_standby_changed(self, event: pgsql.StandbyChangedEvent):
        if event.database != DB_NAME:
            # Leader has not yet set requirements. Wait until next event,
            # or risk connecting to an incorrect database.
            return

        # Charms needing access to the hot standby databases can get
        # their connection details here. Applications can scale out
        # horizontally if they can make use of the read only hot
        # standby replica databases, rather than only use the single
        # master. event.stanbys will be an empty list if no hot standby
        # databases are available.
        self._stored.db_ro_uris = [c.uri for c in event.standbys]

if __name__ == "__main__":
    main(MlflowCharm)
