# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Custom MLflow authentication logic mapping a trusted user-ID header to an MLflow user.

This module is imported by MLflow's basic-auth app (via the ``authorization_function`` key of the
charm-rendered ``basic_auth.ini``) and runs inside the tracking server's workload container. It
authenticates every request from the user-ID header that the surrounding charm ecosystem (identity
stack and service mesh) sets securely, and falls back to HTTP Basic auth only for the in-pod
callers reaching the server over localhost (the charm super-admin and the metrics exporter).

The header name is read from the ``IDENTITY_HEADER_NAME`` environment variable, set by the charm
from its ``identity_header_name`` config.

See https://mlflow.org/docs/3.15.1/self-hosting/security/custom/#using-a-function for details about
custom authentication in MLflow.
"""

from __future__ import annotations

import os
import secrets

import sqlalchemy
from flask import Response, make_response, request
from mlflow import MlflowException
from mlflow.server.auth import authenticate_request_basic_auth, store
from werkzeug.datastructures import Authorization

# TODO: remove once multi-tenancy is completed:
HARDCODED_TEST_IDENTITY = "charm-test-user"
HARDCODED_TEST_WORKSPACE = "default"


def _identity_header_name() -> str:
    """Return the name of the user-ID header to read, as configured by the charm."""
    return os.environ["IDENTITY_HEADER_NAME"]


def _ensure_user_exists(username: str) -> None:
    """Just-in-time provision a dormant, grant-less MLflow user for a newly seen identity.

    The user is created with no grants, so deny-by-default RBAC denies it every operation until it
    is granted access. Its password is random and never used: this user authenticates through the
    trusted identity header, not Basic auth. Provisioning grants presence, never access.
    """
    if store.has_user(username):
        return
    try:
        store.create_user(username, secrets.token_urlsafe(32), is_admin=False)
    except MlflowException as error:
        # NOTE: concurrent workers may race to create the same user on its first request, so the
        # loser's uniqueness violation is treated as a success rather than failing an otherwise
        # valid request, as such an integrity error would indicate that the user already exists:
        if isinstance(error.__cause__, sqlalchemy.exc.IntegrityError):
            return
        raise

    # TODO: remove once multi-tenancy is completed:
    # granting the fixed, hardcoded integration-test identity access in the `default` tenant for
    # integration testing to be still carried out despite the work in progress:
    if username == HARDCODED_TEST_IDENTITY:
        user = store.get_user(username)
        role = store.create_role(f"charm-test-{username}", HARDCODED_TEST_WORKSPACE)
        store.add_role_permission(role.id, "workspace", "*", "USE")
        for resource_type in ("experiment", "registered_model", "prompt"):
            store.add_role_permission(role.id, resource_type, "*", "EDIT")
        store.assign_role_to_user(user.id, role.id)


def authenticate_request() -> Authorization | Response:
    """Authenticate a request, preferring the trusted user-ID header over HTTP Basic auth.

    Returns a werkzeug ``Authorization`` naming the resolved MLflow user on success, or a 401
    ``Response`` when no identity header and no valid Basic credentials are present.
    """
    identity = request.headers.get(_identity_header_name())
    if identity:
        # ensuring that the MLflow user whose username matches the identity header's value either
        # already exists or is successfully created, for downstream permission resolution to work:
        _ensure_user_exists(identity)
        # authenticating the request as the identified MLflow user:
        return Authorization("basic", {"username": identity})

    # authenticating with Basic credentials validated against the auth database when the identity
    # header is not present (only in-pod localhost callers such as the charm super-admin and the
    # metrics exporter can reach here without an identity header).
    if request.authorization is not None:
        return authenticate_request_basic_auth()

    # when neither authentication method is provided, deny because unauthenticated - inspired by:
    # https://github.com/mlflow/mlflow/blob/v3.15.1/mlflow/server/auth/__init__.py#L494-L502
    missing_authentication_resonse = make_response(
        "Not authenticated. Pass either the expected identity header or Basic auth credentials."
    )
    missing_authentication_resonse.status_code = 401
    missing_authentication_resonse.headers["WWW-Authenticate"] = 'Basic realm="mlflow"'
    return missing_authentication_resonse
