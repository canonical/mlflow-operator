"""KubernetesManifests Library

This library implements data transfer for the kubernetes_manifest interface. The library can be used by the requirer
charm to send Kubernetes manifests to the provider charm.

## Getting Started

To get started using the library, fetch the library with `charmcraft`.

```shell
cd some-charm
charmcraft fetch-lib charms.resource_dispatcher.v0.kubernetes_manifests
```

In your charm, the library can be used in two ways depending on whether the manifests
being sent by the charm are static (available when the charm starts up),
or dynamic (for example a manifest template that gets rendered with data from a relation)

If the manifests are static, instantiate the KubernetesManifestsRequirer.
In your charm do:

```python
from charms.resource_dispatcher.v0.kubernetes_manifests import KubernetesManifestsRequirer, KubernetesManifest
# ...

SECRETS_MANIFESTS = [
    KubernetesManifest(
    Path(SECRET1_PATH).read_text()
    ),
    KubernetesManifest(
    Path(SECRET2_PATH).read_text()
    ),
]

SA_MANIFESTS = [
    KubernetesManifest(
    Path(SA1_PATH).read_text()
    ),
    KubernetesManifest(
    Path(SA2_PATH).read_text()
    ),
]

class SomeCharm(CharmBase):
  def __init__(self, *args):
    # ...
    self.secrets_manifests_requirer = KubernetesManifestsRequirer(
            charm=self, relation_name="secrets", manifests_items=SECRETS_MANIFESTS
        )
    self.service_accounts_requirer = KubernetesManifestsRequirer(
            charm=self, relation_name="service-accounts", manifests_items=SA_MANIFESTS
        )
    # ...
```

If the manifests are dynamic, instantiate the KubernetesManifestsRequirerWrapper.
In your charm do:

```python
class SomeCharm(CharmBase):
    def __init__(self, *args):
        # ...
        self._secrets_manifests_wrapper = KubernetesManifestsRequirerWrapper(
            charm = self,
            relation_name = "secrets"
        )
        self._service_accounts_manifests_wrapper = KubernetesManifestsRequirerWrapper(
            charm = self,
            relation_name = "service-accounts"
        )

        self.framework.observe(self.on.leader_elected, self._send_secret)
        self.framework.observe(self.on["secrets"].relation_created, self._send_secret)
        # observe all the other events for when the secrets manifests change

        self.framework.observe(self.on.leader_elected, self._send_service_account)
        self.framework.observe(self.on["service-accounts"].relation_created, self._send_service_account)
        # observe all the other events for when the service accounts manifests change

    def _send_secret(self, _):
        #...
        Write the logic to re-calculate the manifests
        rendered_manifests = ...
        #...
        manifest_items = [KubernetesManifest(rendered_manifests)]
        self._secrets_manifests_wrapper.send_data(manifest_items)


    def _send_service_account(self, _):
        #...
        Write the logic to re-calculate the manifests
        rendered_manifests = ...
        #...
        manifest_items = [KubernetesManifest(rendered_manifests)]
        self._service_accounts_manifests_wrapper.send_data(manifest_items)
```
"""
import json
import logging
import os
from dataclasses import dataclass, field
from typing import List, Optional, Union

import yaml
from ops.charm import CharmBase, RelationEvent
from ops.framework import BoundEvent, EventBase, EventSource, Object, ObjectEvents

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "4254ac012d3640ccbe0ac5380b2436c8"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

KUBERNETES_MANIFESTS_FIELD = "kubernetes_manifests"


@dataclass
class KubernetesManifest:
    """
    Representation of a Kubernetes Object sent to Kubernetes Manifests.

    Args:
        manifest_content: the content of the Kubernetes manifest file
    """

    manifest_content: str
    manifest: dict = field(init=False)

    def __post_init__(self):
        """Validate that the manifest content is a valid YAML."""
        self.manifest = yaml.safe_load(self.manifest_content)


class KubernetesManifestsUpdatedEvent(RelationEvent):
    """Indicates the Kubernetes Objects data was updated."""


class KubernetesManifestsEvents(ObjectEvents):
    """Events for the Kubernetes Manifests library."""

    updated = EventSource(KubernetesManifestsUpdatedEvent)


class KubernetesManifestsProvider(Object):
    """Relation manager for the Provider side of the Kubernetes Manifests relations."""

    on = KubernetesManifestsEvents()

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ):
        """Relation manager for the Provider side of the Kubernetes Manifests relations.

        This relation manager subscribes to:
        * on[relation_name].relation_changed
        * any events provided in refresh_event

        This library emits:
        * KubernetesManifestsUpdatedEvent:
            when data received on the relation is updated

        Args:
            charm: Charm this relation is being used by
            relation_name: Name of this relation (from metadata.yaml)
            refresh_event: List of BoundEvents that this manager should handle.  Use this to update
                           the data sent on this relation on demand.
        """
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name

        self.framework.observe(
            self._charm.on[self._relation_name].relation_changed, self._on_relation_changed
        )

        self.framework.observe(
            self._charm.on[self._relation_name].relation_broken, self._on_relation_broken
        )

        # apply user defined events
        if refresh_event:
            if not isinstance(refresh_event, (tuple, list)):
                refresh_event = [refresh_event]

            for evt in refresh_event:
                self.framework.observe(evt, self._on_relation_changed)

    def get_manifests(self) -> List[dict]:
        """
        Returns a list of dictionaries sent in the data of relation relation_name.
        """

        other_app_to_skip = get_name_of_breaking_app(relation_name=self._relation_name)

        if other_app_to_skip:
            logger.debug(
                f"get_kubernetes_manifests executed during a relation-broken event.  Return will"
                f"exclude {self._relation_name} manifests from other app named '{other_app_to_skip}'.  "
            )

        manifests = []

        kubernetes_manifests_relations = self._charm.model.relations[self._relation_name]

        for relation in kubernetes_manifests_relations:
            other_app = relation.app
            if other_app.name == other_app_to_skip:
                # Skip this app because it is leaving a broken relation
                continue
            json_data = relation.data[other_app].get(KUBERNETES_MANIFESTS_FIELD, "[]")
            manifests.extend(json.loads(json_data))

        return manifests


    def _on_relation_changed(self, event):
        """Handler for relation-changed event for this relation."""
        self.on.updated.emit(event.relation)

    def _on_relation_broken(self, event: BoundEvent):
        """Handler for relation-broken event for this relation."""
        self.on.updated.emit(event.relation)


class KubernetesManifestsRequirer(Object):
    """Relation manager for the Requirer side of the Kubernetes Manifests relation."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        manifests_items: List[KubernetesManifest],
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ):
        """
        Relation manager for the Requirer side of the Kubernetes Manifests relation.

        This relation manager subscribes to:
        * on.leader_elected: because only the leader is allowed to provide this data, and
                             relation_created may fire before the leadership election
        * on[relation_name].relation_created

        * any events provided in refresh_event

        This library emits:
        * (nothing)

        Args:
            charm: Charm this relation is being used by
            relation_name: Name of this relation (from metadata.yaml)
            manifests_items: List of KubernetesManifest objects to send over the relation
            refresh_event: List of BoundEvents that this manager should handle.  Use this to update
                           the data sent on this relation on demand.
        """
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self._manifests_items = manifests_items
        self._requirer_wrapper = KubernetesManifestRequirerWrapper(self._charm, self._relation_name)

        self.framework.observe(self._charm.on.leader_elected, self._send_data)

        self.framework.observe(
            self._charm.on[self._relation_name].relation_created, self._send_data
        )

        # apply user defined events
        if refresh_event:
            if not isinstance(refresh_event, (tuple, list)):
                refresh_event = [refresh_event]

            for evt in refresh_event:
                self.framework.observe(evt, self._send_data)

    def _send_data(self, event: EventBase):
        """Handles any event where we should send data to the relation."""
        self._requirer_wrapper.send_data(self._manifests_items)




class KubernetesManifestRequirerWrapper(Object):
    """
    Wrapper for the relation data sending logic
    """
    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
    ):
        self._charm = charm
        self._relation_name = relation_name

    def _get_manifests_from_items(self, manifests_items: List[KubernetesManifest]):
        return [
            item.manifest for item in manifests_items
        ]

    def send_data(self, manifest_items: List[KubernetesManifest]):
        """Sends the manifests data to the relation in json format."""
        if not self._charm.model.unit.is_leader():
            logger.info(
                "KubernetesManifestsRequirer handled send_data event when it is not the "
                "leader.  Skipping event - no data sent."
            )
            return

        manifests = self._get_manifests_from_items(manifest_items)
        relations = self._charm.model.relations.get(self._relation_name)

        for relation in relations:
            relation_data = relation.data[self._charm.app]
            manifests_as_json = json.dumps(manifests)
            relation_data.update({KUBERNETES_MANIFESTS_FIELD: manifests_as_json})



def get_name_of_breaking_app(relation_name: str) -> Optional[str]:
    """
    Get the name of a remote application that is leaving the relation during a relation broken event by
    checking Juju environment variables.
    If the application name is available, returns the name as a string;
    otherwise None.
    """
    # In the case of a relation-broken event, Juju non-deterministically may or may not include
    # the breaking remote app's data in the relation data bag.  If this data is still in the data
    # bag, the `JUJU_REMOTE_APP` name will always be set.  For these cases, we return the
    # remote app name so the caller can remove that app from the data bag before using it.
    #
    # To catch these cases, we inspect the following environment variables managed by Juju:
    #   JUJU_REMOTE_APP: the name of the app we are interacting with on this relation event
    #   JUJU_RELATION: the name of the relation we are interacting with on this relation event
    #   JUJU_HOOK_NAME: the name of the relation event, such as RELATION_NAME-relation-broken
    # See https://juju.is/docs/sdk/charm-environment-variables for more detail on these variables.
    if not os.environ.get("JUJU_REMOTE_APP", None):
        # No remote app is defined
        return None
    if not os.environ.get("JUJU_RELATION", None) == relation_name:
        # Not this relation
        return None
    if not os.environ.get("JUJU_HOOK_NAME", None) == f"{relation_name}-relation-broken":
        # Not the relation-broken event
        return None

    return os.environ.get("JUJU_REMOTE_APP", None)
