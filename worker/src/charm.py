#!/usr/bin/env python3
# Copyright 2020
# See LICENSE file for licensing details.

import logging

from ops.charm import CharmBase
from ops.main import main
from ops.framework import StoredState

logger = logging.getLogger(__name__)


class WorkerCharm(CharmBase):
    _stored = StoredState()

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.fortune_action, self._on_fortune_action)
        self._stored.set_default(things=[])
        
        # Register relation events
        self.framework.observe(self.on.pod_defaults_relation_joined, self._on_pod_defaults_relation_changed)
        self.framework.observe(self.on.pod_defaults_relation_changed, self._on_pod_defaults_relation_changed)

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

    def _on_pod_defaults_relation_changed(self, event):
        logger.info("================================")
        logger.info(f"_on_pod_defaults_relation_changed is running; {event}")
        logger.info("================================")
        logger.info("================================")
        logger.info(event.relation.data)
        logger.info("================================")
        logger.info("================================")


if __name__ == "__main__":
    main(WorkerCharm)
