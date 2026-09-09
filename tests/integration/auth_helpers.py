# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

# TODO: remove once multi-tenancy is completed:
"""Temporary integration-test helpers for authenticating against the RBAC-enabled tracking server.

The custom authentication module grants this fixed identity coarse `edit` access in the reserved
`default` tenant on first authentication, so the integration tests can exercise the RBAC path by
simply sending it as the user-ID, as in-mesh security against such measures is not yet implemented.
"""

IDENTITY_HEADER_NAME = "kubeflow-userid"
TEST_IDENTITY = "charm-test-user"
TEST_WORKSPACE = "default"


def register_identity_request_header_provider() -> None:
    """Make every ``MlflowClient`` request carry the temporary test user-ID header.

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
