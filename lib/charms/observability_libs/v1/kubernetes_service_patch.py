# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""# [DEPRECATED!] KubernetesServicePatch Library.

The `kubernetes_service_patch` library is DEPRECATED and will be removed in October 2025.

For patching the Kubernetes service created by Juju during the deployment of a charm,
`ops.Unit.set_ports` functionality should be used instead.

"""

import logging
from types import MethodType
from typing import Any, List, Literal, Optional, Union

from lightkube import ApiError, Client  # pyright: ignore
from lightkube.core import exceptions
from lightkube.models.core_v1 import ServicePort, ServiceSpec
from lightkube.models.meta_v1 import ObjectMeta
from lightkube.resources.core_v1 import Service
from lightkube.types import PatchType
from ops import UpgradeCharmEvent
from ops.charm import CharmBase
from ops.framework import BoundEvent, Object

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "0042f86d0a874435adef581806cddbbb"

# Increment this major API version when introducing breaking changes
LIBAPI = 1

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 13

ServiceType = Literal["ClusterIP", "LoadBalancer"]


class KubernetesServicePatch(Object):
    """A utility for patching the Kubernetes service set up by Juju."""

    def __init__(
        self,
        charm: CharmBase,
        ports: List[ServicePort],
        service_name: Optional[str] = None,
        service_type: ServiceType = "ClusterIP",
        additional_labels: Optional[dict] = None,
        additional_selectors: Optional[dict] = None,
        additional_annotations: Optional[dict] = None,
        *,
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ):
        """Constructor for KubernetesServicePatch.

        Args:
            charm: the charm that is instantiating the library.
            ports: a list of ServicePorts
            service_name: allows setting custom name to the patched service. If none given,
                application name will be used.
            service_type: desired type of K8s service. Default value is in line with ServiceSpec's
                default value.
            additional_labels: Labels to be added to the kubernetes service (by default only
                "app.kubernetes.io/name" is set to the service name)
            additional_selectors: Selectors to be added to the kubernetes service (by default only
                "app.kubernetes.io/name" is set to the service name)
            additional_annotations: Annotations to be added to the kubernetes service.
            refresh_event: an optional bound event or list of bound events which
                will be observed to re-apply the patch (e.g. on port change).
                The `install` and `upgrade-charm` events would be observed regardless.
        """
        logger.warning(
            "The ``kubernetes_service_patch v1`` library is DEPRECATED and will be removed "
            "in October 2025. For patching the Kubernetes service created by Juju during "
            "the deployment of a charm, ``ops.Unit.set_ports`` functionality should be used instead."
        )
        super().__init__(charm, "kubernetes-service-patch")
        self.charm = charm
        self.service_name = service_name or self._app
        # To avoid conflicts with the default Juju service, append "-lb" to the service name.
        # The Juju application name is retained for the default service created by Juju.
        if self.service_name == self._app and service_type == "LoadBalancer":
            self.service_name = f"{self._app}-lb"
        self.service_type = service_type
        self.service = self._service_object(
            ports,
            self.service_name,
            service_type,
            additional_labels,
            additional_selectors,
            additional_annotations,
        )

        # Make mypy type checking happy that self._patch is a method
        assert isinstance(self._patch, MethodType)
        # Ensure this patch is applied during the 'install' and 'upgrade-charm' events
        self.framework.observe(charm.on.install, self._patch)
        self.framework.observe(charm.on.upgrade_charm, self._on_upgrade_charm)
        self.framework.observe(charm.on.update_status, self._patch)
        # Sometimes Juju doesn't clean-up a manually created LB service,
        # so we clean it up ourselves just in case.
        self.framework.observe(charm.on.remove, self._remove_service)

        # apply user defined events
        if refresh_event:
            if not isinstance(refresh_event, list):
                refresh_event = [refresh_event]

            for evt in refresh_event:
                self.framework.observe(evt, self._patch)

    def _service_object(
        self,
        ports: List[ServicePort],
        service_name: Optional[str] = None,
        service_type: ServiceType = "ClusterIP",
        additional_labels: Optional[dict] = None,
        additional_selectors: Optional[dict] = None,
        additional_annotations: Optional[dict] = None,
    ) -> Service:
        """Creates a valid Service representation.

        Args:
            ports: a list of ServicePorts
            service_name: allows setting custom name to the patched service. If none given,
                application name will be used.
            service_type: desired type of K8s service. Default value is in line with ServiceSpec's
                default value.
            additional_labels: Labels to be added to the kubernetes service (by default only
                "app.kubernetes.io/name" is set to the service name)
            additional_selectors: Selectors to be added to the kubernetes service (by default only
                "app.kubernetes.io/name" is set to the service name)
            additional_annotations: Annotations to be added to the kubernetes service.

        Returns:
            Service: A valid representation of a Kubernetes Service with the correct ports.
        """
        if not service_name:
            service_name = self._app
        labels = {"app.kubernetes.io/name": self._app}
        if additional_labels:
            labels.update(additional_labels)
        selector = {"app.kubernetes.io/name": self._app}
        if additional_selectors:
            selector.update(additional_selectors)
        return Service(
            apiVersion="v1",
            kind="Service",
            metadata=ObjectMeta(
                namespace=self._namespace,
                name=service_name,
                labels=labels,
                annotations=additional_annotations,  # type: ignore[arg-type]
            ),
            spec=ServiceSpec(
                selector=selector,
                ports=ports,
                type=service_type,
            ),
        )

    def _patch(self, _) -> None:
        """Patch the Kubernetes service created by Juju to map the correct port.

        Raises:
            PatchFailed: if patching fails due to lack of permissions, or otherwise.
        """
        try:
            client = Client()  # pyright: ignore
        except exceptions.ConfigError as e:
            logger.warning("Error creating k8s client: %s", e)
            return

        try:
            if self._is_patched(client):
                return
            if self.service_name != self._app:
                if not self.service_type == "LoadBalancer":
                    self._delete_and_create_service(client)
                else:
                    self._create_lb_service(client)
            client.patch(Service, self.service_name, self.service, patch_type=PatchType.MERGE)
        except ApiError as e:
            if e.status.code == 403:
                logger.error("Kubernetes service patch failed: `juju trust` this application.")
            else:
                logger.error("Kubernetes service patch failed: %s", str(e))
        else:
            logger.info("Kubernetes service '%s' patched successfully", self._app)

    def _delete_and_create_service(self, client: Client):
        service = client.get(Service, self._app, namespace=self._namespace)
        service.metadata.name = self.service_name  # type: ignore[attr-defined]
        service.metadata.resourceVersion = service.metadata.uid = None  # type: ignore[attr-defined]   # noqa: E501
        client.delete(Service, self._app, namespace=self._namespace)
        client.create(service)

    def _create_lb_service(self, client: Client):
        try:
            client.get(Service, self.service_name, namespace=self._namespace)
        except ApiError:
            client.create(self.service)

    def is_patched(self) -> bool:
        """Reports if the service patch has been applied.

        Returns:
            bool: A boolean indicating if the service patch has been applied.
        """
        client = Client()  # pyright: ignore
        return self._is_patched(client)

    def _is_patched(self, client: Client) -> bool:
        # Get the relevant service from the cluster
        try:
            service = client.get(Service, name=self.service_name, namespace=self._namespace)
        except ApiError as e:
            if e.status.code == 404 and self.service_name != self._app:
                return False
            logger.error("Kubernetes service get failed: %s", str(e))
            raise

        # Construct a list of expected ports, should the patch be applied
        expected_ports = [(p.port, p.targetPort) for p in self.service.spec.ports]  # type: ignore[attr-defined]
        # Construct a list in the same manner, using the fetched service
        fetched_ports = [
            (p.port, p.targetPort) for p in service.spec.ports  # type: ignore[attr-defined]
        ]  # noqa: E501
        return expected_ports == fetched_ports

    def _on_upgrade_charm(self, event: UpgradeCharmEvent):
        """Handle the upgrade charm event."""
        # If a charm author changed the service type from LB to ClusterIP across an upgrade, we need to delete the previous LB.
        if self.service_type == "ClusterIP":

            client = Client()  # pyright: ignore

            # Define a label selector to find services related to the app
            selector: dict[str, Any] = {"app.kubernetes.io/name": self._app}

            # Check if any service of type LoadBalancer exists
            services = client.list(Service, namespace=self._namespace, labels=selector)
            for service in services:
                if (
                    not service.metadata
                    or not service.metadata.name
                    or not service.spec
                    or not service.spec.type
                ):
                    logger.warning(
                        "Service patch: skipping resource with incomplete metadata: %s.", service
                    )
                    continue
                if service.spec.type == "LoadBalancer":
                    client.delete(Service, service.metadata.name, namespace=self._namespace)
                    logger.info(f"LoadBalancer service {service.metadata.name} deleted.")

        # Continue the upgrade flow normally
        self._patch(event)

    def _remove_service(self, _):
        """Remove a Kubernetes service associated with this charm.

        Specifically designed to delete the load balancer service created by the charm, since Juju only deletes the
        default ClusterIP service and not custom services.

        Returns:
            None

        Raises:
            ApiError: for deletion errors, excluding when the service is not found (404 Not Found).
        """
        client = Client()  # pyright: ignore

        try:
            client.delete(Service, self.service_name, namespace=self._namespace)
            logger.info("The patched k8s service '%s' was deleted.", self.service_name)
        except ApiError as e:
            if e.status.code == 404:
                # Service not found, so no action needed
                return
            # Re-raise for other statuses
            raise

    @property
    def _app(self) -> str:
        """Name of the current Juju application.

        Returns:
            str: A string containing the name of the current Juju application.
        """
        return self.charm.app.name

    @property
    def _namespace(self) -> str:
        """The Kubernetes namespace we're running in.

        Returns:
            str: A string containing the name of the current Kubernetes namespace.
        """
        with open("/var/run/secrets/kubernetes.io/serviceaccount/namespace", "r") as f:
            return f.read().strip()
