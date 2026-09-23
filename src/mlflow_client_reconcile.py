# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""RBAC reconcile script run by the charm inside the tracking server for mlflow-client requirers.

This script is executed by passing the whole desired set of users' grants across workspaces as a
JSON argument in the following format:
- "users": a list of mappings where each mapping represents a user and contains the following keys:
    - "username":          the requested username
    - "is_super_admin":    whether the requested username is to be promoted to a global super-admin
    - "workspace_grants":  a list of [workspace, tier] pairs representing the user's grants in each
                           workspace
- "protected_admin": the reserved username for the charm's super-admin, to never demote

For each user requested, it get-or-creates the corresponding user and either promotes it to a
global super-admin, when "is_super_admin" is set, or it get-or-creates the requested workspaces and
the respective workspace-scoped roles that grant the user the requested tier in each, to eventually
prune any charm-owned (i.e., previously created by the charm) roles no longer requested and demote
any charm-promoted (i.e., previously promoted by the charm) super-admins no longer requested. Roles
and super-admins not previously created and promoted by the charm are left untouched, as they may
have been created externally by delegated admins and super-admins on the client side and are
to be managed by external users, without having the charm interfere with them. For this reason,
roles created by the charm are always distinguished by means of dedicated name prefixes (but never
MLflow's reserved `__user_<id>__` synthetic roles, which back client self-service grants) and
super-admins promoted by the charm are always tracked by means of marker roles under a reserved
workspace (a marker role needs a workspace to live in, while a super-admin is otherwise not tied to
any workspace).

NOTE: since this script get-or-creates users, workspaces and roles, it is idempotent (i.e., it
tolerates that users, workspaces and/or grants already exist).
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

ROLE_PREFIX_FOR_SUPER_ADMIN = "charm-mlflow-super-admin-"
ROLE_PREFIX_FOR_WORKSPACE_WIDE = "charm-mlflow-client-"
WORKSPACE_FOR_SUPER_ADMIN_MARKER_ROLES = "charm-mlflow-system"
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
# the reconcile exec does not inherit the server's internal backend-store env var that
# _get_workspace_store() reads, so point it at the same backend as the auth store explicitly:
workspace_store = _get_workspace_store(tracking_uri=config.database_uri)

wanted_roles = {}
wanted_super_admin_roles = set()
for entry in payload["users"]:
    user = _get_or_create_user(entry["username"])
    if entry["is_super_admin"]:
        store.update_user(entry["username"], is_admin=True)
        _tolerate_already_exists(
            workspace_store.create_workspace,
            Workspace(name=WORKSPACE_FOR_SUPER_ADMIN_MARKER_ROLES, description=None),
        )
        marker_name = ROLE_PREFIX_FOR_SUPER_ADMIN + str(user.id)
        _get_or_create_role(marker_name, WORKSPACE_FOR_SUPER_ADMIN_MARKER_ROLES)
        wanted_super_admin_roles.add(marker_name)
        continue
    role_name = ROLE_PREFIX_FOR_WORKSPACE_WIDE + str(user.id)
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
    if role.name.startswith(ROLE_PREFIX_FOR_WORKSPACE_WIDE) and role.name not in wanted_roles.get(
        role.workspace, set()
    ):
        store.delete_role(role.id)

# prune the super-admin markers no longer requested, demoting their users (never the charm's own):
usernames_by_id = {user.id: user.username for user in store.list_users()}
for role in store.list_roles([WORKSPACE_FOR_SUPER_ADMIN_MARKER_ROLES]):
    if (
        not role.name.startswith(ROLE_PREFIX_FOR_SUPER_ADMIN)
        or role.name in wanted_super_admin_roles
    ):
        continue
    user_id = int(role.name[len(ROLE_PREFIX_FOR_SUPER_ADMIN) :])  # noqa: E203
    username = usernames_by_id.get(user_id)
    if username and username != protected_admin:
        store.update_user(username, is_admin=False)
    store.delete_role(role.id)
