#!/usr/bin/env python3
# Copyright 2020 Luke Marsden
# See LICENSE file for licensing details.

import logging

from ops.charm import CharmBase
from ops.main import main
from ops.framework import StoredState
from ops.model import ActiveStatus, MaintenanceStatus
from oci_image import OCIImageResource

logger = logging.getLogger(__name__)


class MlflowCharm(CharmBase):
    _stored = StoredState()

    def __init__(self, *args):
        logger.info("================================")
        logger.info("__init__ is running")
        logger.info("================================")
        super().__init__(*args)

        self.image = OCIImageResource(self, 'oci-image')
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
        image_details = self.image.fetch()
        logger.info("================================")
        logger.info(image_details)
        logger.info("================================")

        """
        self.model.pod.set_spec(
            {
                'version': 3,
                'containers': [
                    {
                        'name': 'admission-webhook',
                        'imageDetails': {...},
                        'ports': [{'name': 'webhook', 'containerPort': 443}],
                    }
                ],
            },
        )
        """

        self.model.unit.status = ActiveStatus()



        #self.framework.observe(self.on.config_changed, self._on_config_changed)
        #self.framework.observe(self.on.fortune_action, self._on_fortune_action)
        #self._stored.set_default(things=[])
    """
    def _on_config_changed(self, _):
        current = self.config["thing"]
        if current not in self._stored.things:
            logger.debug("found a new thing: %r", current)
            self._stored.things.append(current)

    def _on_fortune_action(self, event):
        fail = event.params["fail"]
        if fail:
            event.fail(fail)
        else:
            event.set_results({"fortune": "A bug in the code is worth two in the documentation."})
    """

if __name__ == "__main__":
    main(MlflowCharm)
