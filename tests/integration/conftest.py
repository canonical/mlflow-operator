# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from _pytest.config.argparsing import Parser
from auth_helpers import register_identity_request_header_provider

# TODO: remove if authentication via IAM charms is implemented in integration tests:
# making the MLflow client's calls in every test authenticate as the desired test identity the
# custom authentication module of the tracking server grants access to:
register_identity_request_header_provider()


def pytest_addoption(parser: Parser):
    parser.addoption(
        "--charm-path",
        help="Path to charm file for performing tests on.",
    )
