# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

from _pytest.config.argparsing import Parser


def pytest_addoption(parser: Parser):
    parser.addoption(
        "--charm-path",
        help="Path to charm file for performing tests on.",
    )
