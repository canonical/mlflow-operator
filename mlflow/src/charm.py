#!/usr/bin/env python3
# Copyright 2020 Luke Marsden
# See LICENSE file for licensing details.

import logging

from ops.charm import CharmBase
from ops.main import main
from ops.framework import StoredState
from ops.model import ActiveStatus, MaintenanceStatus
logger = logging.getLogger(__name__)

class MlflowCharm(CharmBase):
    _stored = StoredState()

    def __init__(self, *args):
        logger.info("================================")
        logger.info("__init__ is running")
        logger.info("================================")
        super().__init__(*args)

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

if __name__ == "__main__":
    main(MlflowCharm)
