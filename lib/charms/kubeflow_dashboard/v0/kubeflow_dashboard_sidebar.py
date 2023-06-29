"""# KubeflowDashboardSidebar Library
This library implements data transfer for the kubeflow_dashboard_sidebar
interface used by Kubeflow Dashboard to implement the sidebar relation.  This
relation enables applications to request a link on the Kubeflow Dashboard
sidebar dynamically.

To enable an application to add a link to Kubeflow Dashboard's sidebar, use
the KubeflowDashboardSidebarRequirer and SidebarItem classes included here as
shown below.  No additional action is required within the charm.  On
establishing the relation, the data will be sent to Kubeflow Dashboard to add
the link.  The link will be removed if the relation is broken.

## Getting Started

To get started using the library, fetch the library with `charmcraft`.

```shell
cd some-charm
charmcraft fetch-lib charms.kubeflow_dashboard.v0.kubeflow_dashboard_sidebar
```

Then in your charm, do:

```python
from charms.kubeflow_dashboard.v0.kubeflow_dashboard_sidebar import (
    KubeflowDashboardSidebarRequirer,
    SidebarItem,
)
# ...

SIDEBAR_ITEMS = [
    SidebarItem(
        text="Example Relative Link",
        link="/relative-link",
        type="item",
        icon="assessment"
    ),
    SidebarItem(
        text="Example External Link",
        link="https://charmed-kubeflow.io/docs",
        type="item",
        icon="assessment"
    ),
]

class SomeCharm(CharmBase):
  def __init__(self, *args):
    # ...
    self.kubeflow_dashboard_sidebar = KubeflowDashboardSidebarRequirer(
        charm=self,
        relation_name="sidebar",  # use whatever you call the relation in your metadata.yaml
        SIDEBAR_ITEMS
    )
    # ...
```
"""
import os
from dataclasses import dataclass, asdict
import json
import logging

from typing import List, Optional, Union
from ops.charm import CharmBase, RelationEvent
from ops.framework import Object, ObjectEvents, EventSource, BoundEvent, EventBase

logger = logging.getLogger(__name__)

# The unique Charmhub library identifier, never change it
LIBID = "a5795a88ee31458f9bc3ae026a04b89f"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


SIDEBAR_ITEMS_FIELD = "sidebar_items"


@dataclass
class SidebarItem:
    """Representation of a Kubeflow Dashboard sidebar entry.

    See https://www.kubeflow.org/docs/components/central-dash/customizing-menu/ for more details.

    Args:
        text: The text shown in the sidebar
        link: The relative link within the host (eg: /runs, not http://.../runs)
        type: A type of sidebar entry (typically, "item")
        icon: An icon for the link, from
              https://kevingleason.me/Polymer-Todo/bower_components/iron-icons/demo/index.html
    """
    text: str
    link: str
    type: str  # noqa: A003
    icon: str


class KubeflowDashboardSidebarDataUpdatedEvent(RelationEvent):
    """Indicates the Kubeflow Dashboard Sidebar data was updated."""


class KubeflowDashboardidebarEvents(ObjectEvents):
    """Events for the Kubeflow Dashboard Sidebar library."""

    data_updated = EventSource(KubeflowDashboardSidebarDataUpdatedEvent)


class KubeflowDashboardSidebarProvider(Object):
    """Relation manager for the Provider side of the Kubeflow Dashboard Sidebar relation.."""
    on = KubeflowDashboardidebarEvents()

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ):
        """Relation manager for the Provider side of the Kubeflow Dashboard Sidebar relation.

        This relation manager subscribes to:
        * on[relation_name].relation_changed
        * any events provided in refresh_event

        This library emits:
        * KubeflowDashboardSidebarDataUpdatedEvent:
            when data received on the relation is updated

        TODO: Should this class automatically subscribe to events, or should it optionally do that.
          The former is typical of charm libraries, the latter lets the user better control and
          visibility on how it is used.

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

    def get_sidebar_items(self, omit_breaking_app: bool = True) -> List[SidebarItem]:
        """Returns a list of all SidebarItems from related Applications.

        Args:
            omit_breaking_app: If True and this is called during a sidebar-relation-broken event,
                               the remote app's data will be omitted.  For more context, see:
                               https://github.com/canonical/kubeflow-dashboard-operator/issues/124

        Returns:
            List of SidebarItems defining the dashboard sidebar for all related applications.
        """
        # If this is a relation-broken event, remove the departing app from the relation data if
        # it exists.  See: https://github.com/canonical/kubeflow-dashboard-operator/issues/124
        if omit_breaking_app:
            other_app_to_skip = get_name_of_breaking_app(relation_name=self._relation_name)
        else:
            other_app_to_skip = None

        if other_app_to_skip:
            logger.debug(
                f"get_sidebar_items executed during a relation-broken event.  Return will"
                f"exclude sidebar_items from other app named '{other_app_to_skip}'.  "
            )

        sidebar_items = []
        sidebar_relation = self.model.relations[self._relation_name]
        for relation in sidebar_relation:
            other_app = relation.app
            if other_app.name == other_app_to_skip:
                # Skip this app because it is leaving a broken relation
                continue
            json_data = relation.data[other_app].get(SIDEBAR_ITEMS_FIELD, "{}")
            dict_data = json.loads(json_data)
            sidebar_items.extend([SidebarItem(**item) for item in dict_data])

        return sidebar_items

    def get_sidebar_items_as_json(self, omit_breaking_app: bool = True) -> str:
        """Returns a JSON string of all SidebarItems from related Applications.

        Args:
            omit_breaking_app: If True and this is called during a sidebar-relation-broken event,
                               the remote app's data will be omitted.  For more context, see:
                               https://github.com/canonical/kubeflow-dashboard-operator/issues/124

        Returns:
            JSON string of all SidebarItems for all related applications, each as dicts.
        """
        return sidebar_items_to_json(self.get_sidebar_items(omit_breaking_app=omit_breaking_app))

    def _on_relation_changed(self, event):
        """Handler for relation-changed event for this relation."""
        self.on.data_updated.emit(event.relation)

    def _on_relation_broken(self, event: BoundEvent):
        """Handler for relation-broken event for this relation."""
        self.on.data_updated.emit(event.relation)


class KubeflowDashboardSidebarRequirer(Object):
    """Relation manager for the Requirer side of the Kubeflow Dashboard Sidebar relation."""
    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        sidebar_items: List[SidebarItem],
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ):
        """
        Relation manager for the Requirer side of the Kubeflow Dashboard Sidebar relation.

        This relation manager subscribes to:
        * on.leader_elected: because only the leader is allowed to provide this data, and
                             relation_created may fire before the leadership election
        * on[relation_name].relation_created

        * any events provided in refresh_event

        This library emits:
        * (nothing)

        TODO: Should this class automatically subscribe to events, or should it optionally do that.
          The former is typical of charm libraries, the latter lets the user better control and
          visibility on how it is used.

        Args:
            charm: Charm this relation is being used by
            relation_name: Name of this relation (from metadata.yaml)
            sidebar_items: List of SidebarItem objects to send over the relation
            refresh_event: List of BoundEvents that this manager should handle.  Use this to update
                           the data sent on this relation on demand.
        """
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self._sidebar_items = sidebar_items

        self.framework.observe(self._charm.on.leader_elected, self._on_send_data)

        self.framework.observe(
            self._charm.on[self._relation_name].relation_created, self._on_send_data
        )

        # apply user defined events
        if refresh_event:
            if not isinstance(refresh_event, (tuple, list)):
                refresh_event = [refresh_event]

            for evt in refresh_event:
                self.framework.observe(evt, self._on_send_data)

    def _on_send_data(self, event: EventBase):
        """Handles any event where we should send data to the relation."""
        if not self._charm.model.unit.is_leader():
            logger.info(
                "KubeflowDashboardSidebarRequirer handled send_data event when it is not the "
                "leader.  Skipping event - no data sent."
            )
            return

        relations = self._charm.model.relations.get(self._relation_name)

        for relation in relations:
            relation_data = relation.data[self._charm.app]
            sidebar_items_as_json = json.dumps([asdict(item) for item in self._sidebar_items])
            relation_data.update({SIDEBAR_ITEMS_FIELD: sidebar_items_as_json})


def get_name_of_breaking_app(relation_name: str) -> Optional[str]:
    """Returns breaking app name if called during RELATION_NAME-relation-broken and the breaking app name is available.  # noqa

    Else, returns None.

    Relation type and app name are inferred from juju environment variables.
    """
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


def sidebar_items_to_json(sidebar_items: List[SidebarItem]) -> str:
    """Returns a list of SidebarItems as a JSON string."""
    return json.dumps(
        [asdict(sidebar_item) for sidebar_item in sidebar_items]
    )
