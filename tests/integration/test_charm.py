# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#

"""Integration tests for Seldon Core Operator/Charm."""

import logging
from pathlib import Path

import aiohttp
import pytest
import requests
import tenacity
import yaml
from lightkube import Client
from lightkube.generic_resource import create_namespaced_resource
from lightkube.resources.apps_v1 import Deployment
from lightkube.resources.core_v1 import Namespace, Service
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]
RELATIONAL_DB_CHARM_NAME = "charmed-osm-mariadb-k8s"
OBJECT_STORAGE_CHARM_NAME = "minio"
OBJECT_STORAGE_CONFIG = {
    "access-key": "minio",
    "secret-key": "minio123",
    "port": "9000",
}


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    """Build and deploy the charm.

    Assert on the unit status.
    """
    charm_under_test = await ops_test.build_charm(".")
    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}

    await ops_test.model.deploy(
        charm_under_test, resources=resources, application_name=CHARM_NAME, trust=True
    )

    await ops_test.model.wait_for_idle(
        apps=[CHARM_NAME], status="waiting", raise_on_blocked=True, timeout=300
    )
    assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "waiting"
    assert (
        ops_test.model.applications[CHARM_NAME].units[0].workload_status_message
        == "Waiting for object-storage relation data"
    )


@pytest.mark.abort_on_fail
async def test_add_relational_db_with_relation_expect_active(ops_test: OpsTest):
    await ops_test.model.deploy(OBJECT_STORAGE_CHARM_NAME, config=OBJECT_STORAGE_CONFIG)
    await ops_test.model.relate(OBJECT_STORAGE_CHARM_NAME, CHARM_NAME)
    await ops_test.model.deploy(RELATIONAL_DB_CHARM_NAME, channel="latest/edge", trust=True)
    await ops_test.model.relate(RELATIONAL_DB_CHARM_NAME, CHARM_NAME)

    await ops_test.model.wait_for_idle(
        apps=[CHARM_NAME], status="active", raise_on_blocked=True, timeout=300, idle_period=30
    )
    assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "active"

