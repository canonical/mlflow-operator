#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
#

import logging

from charmed_kubeflow_chisme.exceptions import ErrorWithStatus
from charms.observability_libs.v1.kubernetes_service_patch import KubernetesServicePatch
from lightkube.models.core_v1 import ServicePort
from ops.charm import CharmBase
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, MaintenanceStatus, WaitingStatus
from ops.pebble import ChangeError, Layer


class MlflowCharm(CharmBase):
    """A Juju Charm for MLFlow."""

    def __init__(self, *args):
        super().__init__(*args)

        self.logger = logging.getLogger(__name__)
        self._port = self.model.config["mlflow_port"]
        self._container_name = "mlflow-server"
        self._container = self.unit.get_container(self._container_name)

        self.framework.observe(self.on.upgrade_charm, self._on_event)
        self.framework.observe(self.on.config_changed, self._on_event)
        self.framework.observe(self.on.mlflow_server_pebble_ready, self._on_pebble_ready)

        self._create_service()

    @property
    def container(self):
        """Return container."""
        return self._container

    def _create_service(self):
        """Create k8s service based on charm'sconfig."""
        if self.config["enable_mlflow_nodeport"]:
            service_type = "NodePort"
            self._node_port = self.model.config["mlflow_nodeport"]
            port = ServicePort(
                int(self._port),
                name=f"{self.app.name}",
                targetPort=int(self._port),
                nodePort=int(self._node_port),
            )
        else:
            service_type = "ClusterIP"
            port = ServicePort(int(self._port), name=f"{self.app.name}")
        self.service_patcher = KubernetesServicePatch(
            self,
            [port],
            service_type=service_type,
            service_name=f"{self.model.app.name}",
            refresh_event=self.on.config_changed,
        )

    def _charmed_mlflow_layer(self) -> Layer:
        """Create and return Pebble framework layer."""

        layer_config = {
            "summary": "mlflow-server layer",
            "description": "Pebble config layer for mlflow-server",
            "services": {
                self._container_name: {
                    "override": "replace",
                    "summary": "Entrypoint of mlflow-server image",
                    "command": (
                        "mlflow " "server " "--host " "0.0.0.0 " "--port " f"{self._port} "
                    ),
                    "startup": "enabled",
                }
            },
        }

        return Layer(layer_config)

    def _check_leader(self):
        """Check if this unit is a leader."""
        if not self.unit.is_leader():
            self.logger.info("Not a leader, skipping setup")
            raise ErrorWithStatus("Waiting for leadership", WaitingStatus)

    def _update_layer(self) -> None:
        """Update the Pebble configuration layer (if changed)."""
        current_layer = self.container.get_plan()
        new_layer = self._charmed_mlflow_layer()
        if current_layer.services != new_layer.services:
            self.unit.status = MaintenanceStatus("Applying new pebble layer")
            self.container.add_layer(self._container_name, new_layer, combine=True)
            try:
                self.logger.info("Pebble plan updated with new configuration, replaning")
                self.container.replan()
            except ChangeError as err:
                raise ErrorWithStatus(f"Failed to replan with error: {str(err)}", BlockedStatus)

    def _on_pebble_ready(self, _):
        """Configure started container."""
        if not self.container.can_connect():
            # Pebble Ready event should indicate that container is available
            raise ErrorWithStatus("Pebble is ready and container is not ready", BlockedStatus)

        # proceed with other actions
        self._on_event(_)

    def _on_event(self, event) -> None:
        """Perform all required actions for the Charm."""
        try:
            self._check_leader()
            self._update_layer()
        except ErrorWithStatus as err:
            self.model.unit.status = err.status
            self.logger.info(f"Event {event} stopped early with message: {str(err)}")
            return
        self.model.unit.status = ActiveStatus()


if __name__ == "__main__":
    main(MlflowCharm)
