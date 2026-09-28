# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

# TODO: remove if authentication via IAM charms is implemented in integration tests:
"""Integration-test helpers to set user-ID headers on MLflow client requests while not
authenticating via IAM charms.
"""

IDENTITY_HEADER_NAME = "kubeflow-userid"
TEST_IDENTITY = "my-iam-user-identity"


def register_identity_request_header_provider() -> None:
    """Make every ``MlflowClient`` request carry the test user-ID header, just like the IAM charms
    would inject every request with such a header after JWT-based bearer-authentication at the IAM
    level.

    Registers a request-header provider with MLflow's registry so the client-library calls (which,
    unlike raw ``requests``, do not let a test set arbitrary headers) authenticate as the test
    identity.
    """
    from mlflow.tracking.request_header.registry import _request_header_provider_registry

    class _IdentityHeaderProvider:
        def in_context(self):
            return True

        def request_headers(self):
            return {IDENTITY_HEADER_NAME: TEST_IDENTITY}

    _request_header_provider_registry.register(_IdentityHeaderProvider)
