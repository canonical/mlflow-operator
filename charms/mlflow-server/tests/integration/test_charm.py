# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path
from time import sleep

import pytest
import yaml
from lightkube.core.client import Client
from lightkube.models.rbac_v1 import PolicyRule
from lightkube.resources.rbac_authorization_v1 import Role
from pytest_lazyfixture import lazy_fixture
from pytest_operator.plugin import OpsTest
from selenium.common.exceptions import JavascriptException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions
from selenium.webdriver.support.ui import WebDriverWait
from seleniumwire import webdriver

log = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./metadata.yaml").read_text())
CHARM_NAME = METADATA["name"]


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    db = "mlflow-db"
    obj_storage = "minio"
    await ops_test.model.deploy("charmed-osm-mariadb-k8s", application_name=db)
    await ops_test.model.deploy(obj_storage)

    my_charm = await ops_test.build_charm(".")
    image_path = METADATA["resources"]["oci-image"]["upstream-source"]
    resources = {"oci-image": image_path}
    await ops_test.model.deploy(my_charm, resources=resources)
    await ops_test.model.add_relation(CHARM_NAME, obj_storage)
    await ops_test.model.add_relation(CHARM_NAME, db)
    await ops_test.model.wait_for_idle(status="active")


@pytest.mark.assertions
async def test_successful_deploy(ops_test: OpsTest):
    assert ops_test.model.applications[CHARM_NAME].units[0].workload_status == "active"


@pytest.mark.abort_on_fail
async def test_deploy_with_ingress(ops_test: OpsTest):
    istio_pilot = "istio-pilot"
    istio_gateway = "istio-gateway"
    await ops_test.model.deploy(istio_pilot, channel="1.5/stable")
    await ops_test.model.deploy(istio_gateway, channel="1.5/stable")
    await ops_test.model.add_relation(istio_gateway, istio_pilot)
    await ops_test.model.add_relation(istio_pilot, CHARM_NAME)

    await ops_test.model.wait_for_idle(
        [istio_gateway],
        status="waiting",
        timeout=600,
    )

    lightkube_client = Client(
        namespace=ops_test.model_name,
    )

    await ops_test.model.set_config({"update-status-hook-interval": "15s"})
    istio_gateway_role_name = "istio-gateway-operator"

    new_policy_rule = PolicyRule(verbs=["*"], apiGroups=["*"], resources=["*"])
    this_role = lightkube_client.get(Role, istio_gateway_role_name)
    this_role.rules.append(new_policy_rule)
    lightkube_client.patch(Role, istio_gateway_role_name, this_role)

    sleep(50)
    await ops_test.model.set_config({"update-status-hook-interval": "5m"})

    await ops_test.model.wait_for_idle(status="active")


@pytest.fixture
async def url_with_ingress(ops_test: OpsTest):
    status = await ops_test.model.get_status()
    url = f"http://{status['applications']['istio-gateway']['public-address']}.nip.io/mlflow/"
    yield url


@pytest.fixture
async def url_without_ingress(ops_test: OpsTest):
    status = await ops_test.model.get_status()
    unit_name = ops_test.model.applications[CHARM_NAME].units[0].name
    url = f"http://{status['applications'][CHARM_NAME]['units'][unit_name]['address']}:5000"
    yield url


@pytest.mark.assertions
@pytest.mark.parametrize(
    "url", [lazy_fixture("url_without_ingress"), lazy_fixture("url_with_ingress")]
)
async def test_access_dashboard(request, url):
    options = Options()
    options.headless = True
    options.log.level = "trace"
    max_wait = 20  # seconds

    kwargs = {
        "options": options,
        "seleniumwire_options": {"enable_har": True},
    }

    with webdriver.Firefox(**kwargs) as driver:
        wait = WebDriverWait(driver, max_wait, 1, (JavascriptException, StopIteration))
        for _ in range(60):
            try:
                driver.get(url)
                wait.until(
                    expected_conditions.presence_of_element_located(
                        (By.CLASS_NAME, "experiment-view-container")
                    )
                )
                break
            except WebDriverException:
                sleep(5)
        else:
            driver.get(url)
        wait.until(
            expected_conditions.presence_of_element_located(
                (By.CLASS_NAME, "experiment-view-container")
            )
        )
        Path(f"/tmp/selenium-{request.node.name}.har").write_text(driver.har)
