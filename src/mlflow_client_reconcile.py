# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""RBAC reconcile script run by the charm inside the tracking server for mlflow-client requirers.

Executed over the whole desired set of user grants across workspaces, passed as a JSON argument: a
mapping with a ``users`` list (each ``{username, is_super_admin, workspace_grants}``, where
``workspace_grants`` is a list of ``[workspace, tier]`` pairs) and the ``protected_admin`` username
to never demote. For each user it get-or-creates the user and either promotes it to a global
super-admin (the MLflow ``is_admin`` flag) or get-or-creates the requested workspaces and one
charm-owned, workspace-scoped role granting the requested tier in each. It then prunes any
charm-owned role no longer requested and demotes any charm-promoted super-admin no longer
requested.

NOTE:
- it is idempotent (it is tolerated that users, workspaces and/or grants may already exist)
- it drives the auth and workspace stores directly (no running server needed), via the same
  environment the tracking server uses, inherited by running it in the workload service context;
- it owns only roles under dedicated prefixes, never MLflow's reserved `__user_<id>__` synthetic
  roles (which back client self-service grants), and retains workspaces and users on pruning;
- charm-promoted super-admins are tracked by a marker role under a reserved workspace, so only
  users the charm itself promoted are ever demoted, and never the charm's own super-admin.
"""

import json
import secrets
import sys

import sqlalchemy
from mlflow import MlflowException
from mlflow.entities.workspace import Workspace
from mlflow.server.auth import store
from mlflow.server.auth.config import read_auth_config
from mlflow.server.workspace_helpers import _get_workspace_store

# prefix of the workspace-scoped roles the charm owns for per-workspace client grants:
ROLE_PREFIX = "charm-mlflow-client-"
# prefix of the marker roles the charm owns to record which users it promoted to super-admin:
SUPER_ADMIN_ROLE_PREFIX = "charm-mlflow-super-admin-"
# reserved workspace hosting the super-admin marker roles (a marker role needs a workspace to live
# in, while a super-admin is otherwise not tied to any workspace):
SYSTEM_WORKSPACE = "charm-mlflow-system"
DEFAULT_TIER = "edit"
# every concrete (non-workspace) MLflow resource type, so type-wide tiers cover them all, from:
# https://github.com/mlflow/mlflow/blob/v3.15.1/mlflow/server/auth/permissions.py#L111-L121
# NOTE: keep this list up to date with the linked upstream source, when upgrading MLflow:
RESOURCE_TYPES = (
    "experiment",
    "registered_model",
    "prompt",
    "scorer",
    "gateway_secret",
    "gateway_endpoint",
    "gateway_model_definition",
    "mcp_server",
)
TIERS_TO_NATIVE_GRANTS = {
    "admin": [("workspace", "*", "MANAGE")],
    "edit": [("workspace", "*", "USE")] + [(t, "*", "EDIT") for t in RESOURCE_TYPES],
    "member": [("workspace", "*", "USE")],
    "read-only": [(t, "*", "READ") for t in RESOURCE_TYPES],
}


def _already_exists(error):
    """Return whether an MLflow error represents an idempotent "already exists" outcome."""
    return getattr(error, "error_code", None) == "RESOURCE_ALREADY_EXISTS" or isinstance(
        getattr(error, "__cause__", None), sqlalchemy.exc.IntegrityError
    )


def _tolerate_already_exists(mutation, *args):
    """Run a store mutation, tolerating an idempotent "already exists" outcome."""
    try:
        mutation(*args)
    except MlflowException as error:
        if not _already_exists(error):
            raise


def _get_or_create_user(username):
    """Return the MLflow user, creating it (non-admin, with a random password) if absent."""
    if not store.has_user(username):
        _tolerate_already_exists(store.create_user, username, secrets.token_urlsafe(32), False)
    return store.get_user(username)


def _get_or_create_role(role_name, workspace):
    """Return the workspace-scoped charm-owned role, creating it if absent."""
    try:
        return store.create_role(role_name, workspace)
    except MlflowException as error:
        if not _already_exists(error):
            raise
        return next(r for r in store.list_roles([workspace]) if r.name == role_name)


payload = json.loads(sys.argv[1])
protected_admin = payload["protected_admin"]

config = read_auth_config()
store.init_db(config.database_uri, read_db_uri=config.read_database_uri)
workspace_store = _get_workspace_store()

wanted_roles = {}
wanted_super_admin_roles = set()
for entry in payload["users"]:
    user = _get_or_create_user(entry["username"])
    if entry["is_super_admin"]:
        store.update_user(entry["username"], is_admin=True)
        _tolerate_already_exists(
            workspace_store.create_workspace, Workspace(name=SYSTEM_WORKSPACE, description=None)
        )
        marker_name = SUPER_ADMIN_ROLE_PREFIX + str(user.id)
        _get_or_create_role(marker_name, SYSTEM_WORKSPACE)
        wanted_super_admin_roles.add(marker_name)
        continue
    role_name = ROLE_PREFIX + str(user.id)
    for workspace, tier in entry["workspace_grants"]:
        _tolerate_already_exists(
            workspace_store.create_workspace, Workspace(name=workspace, description=None)
        )
        role = _get_or_create_role(role_name, workspace)
        native_grants = TIERS_TO_NATIVE_GRANTS.get(tier, TIERS_TO_NATIVE_GRANTS[DEFAULT_TIER])
        for resource_type, resource_pattern, permission in native_grants:
            _tolerate_already_exists(
                store.add_role_permission, role.id, resource_type, resource_pattern, permission
            )
        _tolerate_already_exists(store.assign_role_to_user, user.id, role.id)
        wanted_roles.setdefault(workspace, set()).add(role_name)

# prune the charm-owned per-workspace roles that are no longer requested:
for role in store.list_roles():
    if role.name.startswith(ROLE_PREFIX) and role.name not in wanted_roles.get(
        role.workspace, set()
    ):
        store.delete_role(role.id)

# prune the super-admin markers no longer requested, demoting their users (never the charm's own):
usernames_by_id = {user.id: user.username for user in store.list_users()}
for role in store.list_roles([SYSTEM_WORKSPACE]):
    if not role.name.startswith(SUPER_ADMIN_ROLE_PREFIX) or role.name in wanted_super_admin_roles:
        continue
    username = usernames_by_id.get(int(role.name[len(SUPER_ADMIN_ROLE_PREFIX) :]))  # noqa: E203
    if username and username != protected_admin:
        store.update_user(username, is_admin=False)
    store.delete_role(role.id)
