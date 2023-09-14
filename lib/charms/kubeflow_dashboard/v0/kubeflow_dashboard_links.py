"""KubeflowDashboardLinks Library
This library implements data transfer for the kubeflow_dashboard_links
interface used by Kubeflow Dashboard to implement the links relation.  This
relation enables applications to request a link on the Kubeflow Dashboard
dynamically.

To enable an application to add a link to Kubeflow Dashboard, use
the KubeflowDashboardLinksRequirer and DashboardLink classes included here as
shown below.  No additional action is required within the charm.  On
establishing the relation, the data will be sent to Kubeflow Dashboard to add
the links.  The links will be removed if the relation is broken.

## Getting Started

To get started using the library, fetch the library with `charmcraft`.

```shell
cd some-charm
charmcraft fetch-lib charms.kubeflow_dashboard.v0.kubeflow_dashboard_links
```

Then in your charm, do:

```python
from charms.kubeflow_dashboard.v0.kubeflow_dashboard_links import (
    KubeflowDashboardLinksRequirer,
    DashboardLink,
)
# ...

DASHBOARD_LINKS = [
    DashboardLink(
        text="Example Relative Link",
        link="/relative-link",
        type="item",
        icon="assessment",
        location="sidebar",
    ),
    DashboardLink(
        text="Example External Link",
        link="https://charmed-kubeflow.io/docs",
        type="item",
        icon="assessment",
        location="sidebar-external"
    ),
]

class SomeCharm(CharmBase):
  def __init__(self, *args):
    # ...
    self.kubeflow_dashboard_links = KubeflowDashboardLinksRequirer(
        charm=self,
        relation_name="links",  # use whatever you call the relation in your metadata.yaml
        DASHBOARD_LINKS
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
LIBID = "635fdbfc0fcc420882835d4c0086bb5d"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 2


DASHBOARD_LINK_LOCATIONS = ['menu', 'external', 'quick', 'documentation']
DASHBOARD_LINKS_FIELD = "dashboard_links"


@dataclass
class DashboardLink:
    """Representation of a Kubeflow Dashboard Link entry.

    See https://www.kubeflow.org/docs/components/central-dash/customizing-menu/ for more details.

    Args:
        text: The text shown for the link
        link: The link (a relative link for `location=sidebar` or `location=quick`, eg: `/mlflow`,
              or a full URL for other locations, eg: http://my-website.com)
        type: A type of sidebar entry (typically, "item")
        icon: An icon for the link, from
              https://kevingleason.me/Polymer-Todo/bower_components/iron-icons/demo/index.html
        location: Link's location on the dashboard.  One of `sidebar`, `sidebar_external`, `quick`,
                  and `documentation`.
    """

    text: str
    link: str
    location: str
    icon: str = "icons:link"
    type: str = "item"  # noqa: A003
    desc: str = ""


class KubeflowDashboardLinksUpdatedEvent(RelationEvent):
    """Indicates the Kubeflow Dashboard link data was updated."""


class KubeflowDashboardLinksEvents(ObjectEvents):
    """Events for the Kubeflow Dashboard Links library."""

    updated = EventSource(KubeflowDashboardLinksUpdatedEvent)


class KubeflowDashboardLinksProvider(Object):
    """Relation manager for the Provider side of the Kubeflow Dashboard Sidebar relation.."""

    on = KubeflowDashboardLinksEvents()

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ):
        """Relation manager for the Provider side of the Kubeflow Dashboard Links relation.

        This relation manager subscribes to:
        * on[relation_name].relation_changed
        * any events provided in refresh_event

        This library emits:
        * KubeflowDashboardLinksUpdatedEvent:
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

    def get_dashboard_links(
        self, omit_breaking_app: bool = True, location: Optional[str] = None
    ) -> List[DashboardLink]:
        """Returns a list of all DashboardItems from related Applications.

        Args:
            omit_breaking_app: If True and this is called during a link-relation-broken event,
                               the remote app's data will be omitted.  For more context, see:
                               https://github.com/canonical/kubeflow-dashboard-operator/issues/124
            location: If specified, return only links with this location.  Else, returns all links.

        Returns:
            List of DashboardLinks defining the dashboard links for all related applications.
        """
        # If this is a relation-broken event, remove the departing app from the relation data if
        # it exists.  See: https://github.com/canonical/kubeflow-dashboard-operator/issues/124
        if omit_breaking_app:
            other_app_to_skip = get_name_of_breaking_app(relation_name=self._relation_name)
        else:
            other_app_to_skip = None

        if other_app_to_skip:
            logger.debug(
                f"get_dashboard_links executed during a relation-broken event.  Return will"
                f"exclude dashboard_links from other app named '{other_app_to_skip}'.  "
            )

        dashboard_links = []
        dashboard_link_relation = self.model.relations[self._relation_name]
        for relation in dashboard_link_relation:
            other_app = relation.app
            if other_app.name == other_app_to_skip:
                # Skip this app because it is leaving a broken relation
                continue
            json_data = relation.data[other_app].get(DASHBOARD_LINKS_FIELD, "{}")
            dict_data = json.loads(json_data)
            dashboard_links.extend([DashboardLink(**item) for item in dict_data])

        if location is not None:
            dashboard_links = [
                dashboard_link
                for dashboard_link in dashboard_links
                if dashboard_link.location == location
            ]

        return dashboard_links

    def get_dashboard_links_as_json(
        self, omit_breaking_app: bool = True, location: Optional[str] = None
    ) -> str:
        """Returns a JSON string of all DashboardItems from related Applications.

        Args:
            omit_breaking_app: If True and this is called during a links-relation-broken event,
                               the remote app's data will be omitted.  For more context, see:
                               https://github.com/canonical/kubeflow-dashboard-operator/issues/124
            location: If specified, return only links with this location.  Else, returns all links.

        Returns:
            JSON string of all DashboardLinks for all related applications, each as dicts.
        """
        return dashboard_links_to_json(
            self.get_dashboard_links(omit_breaking_app=omit_breaking_app)
        )

    def _on_relation_changed(self, event):
        """Handler for relation-changed event for this relation."""
        self.on.updated.emit(event.relation)

    def _on_relation_broken(self, event: BoundEvent):
        """Handler for relation-broken event for this relation."""
        self.on.updated.emit(event.relation)


class KubeflowDashboardLinksRequirer(Object):
    """Relation manager for the Requirer side of the Kubeflow Dashboard Links relation."""

    def __init__(
        self,
        charm: CharmBase,
        relation_name: str,
        dashboard_links: List[DashboardLink],
        refresh_event: Optional[Union[BoundEvent, List[BoundEvent]]] = None,
    ):
        """
        Relation manager for the Requirer side of the Kubeflow Dashboard Link relation.

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
            dashboard_links: List of DashboardLink objects to send over the relation
            refresh_event: List of BoundEvents that this manager should handle.  Use this to update
                           the data sent on this relation on demand.
        """
        super().__init__(charm, relation_name)
        self._charm = charm
        self._relation_name = relation_name
        self._dashboard_links = dashboard_links

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
                "KubeflowDashboardLinksRequirer handled send_data event when it is not the "
                "leader.  Skipping event - no data sent."
            )
            return

        relations = self._charm.model.relations.get(self._relation_name)

        for relation in relations:
            relation_data = relation.data[self._charm.app]
            dashboard_links_as_json = json.dumps([asdict(item) for item in self._dashboard_links])
            relation_data.update({DASHBOARD_LINKS_FIELD: dashboard_links_as_json})


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


def dashboard_links_to_json(dashboard_links: List[DashboardLink]) -> str:
    """Returns a list of SidebarItems as a JSON string."""
    return json.dumps([asdict(dashboard_link) for dashboard_link in dashboard_links])
