from pathlib import Path
from subprocess import check_output
from time import sleep

import pytest
import yaml
from selenium.common.exceptions import JavascriptException, WebDriverException
from selenium.webdriver.common.by import By
from selenium.webdriver.firefox.options import Options
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait
from seleniumwire import webdriver


@pytest.fixture()
def driver(request):
    status = yaml.safe_load(
        check_output(
            [
                "kubectl",
                "get",
                "services",
                "-A",
                "-oyaml",
            ]
        )
    )
    endpoint = next(
        s["status"]["loadBalancer"]["ingress"][0]["ip"]
        for s in status["items"]
        if s["metadata"]["name"] == "istio-ingressgateway"
    )
    url = f"http://{endpoint}.nip.io/mlflow/"
    options = Options()
    options.headless = True
    options.log.level = "trace"

    kwargs = {
        "options": options,
        "seleniumwire_options": {"enable_har": True},
    }

    with webdriver.Firefox(**kwargs) as driver:
        wait = WebDriverWait(driver, 180, 1, (JavascriptException, StopIteration))
        for _ in range(60):
            try:
                driver.get(url)
                break
            except WebDriverException:
                sleep(5)
        else:
            driver.get(url)

        yield driver, wait, url

        Path(f"/tmp/selenium-{request.node.name}.har").write_text(driver.har)
        driver.get_screenshot_as_file(f"/tmp/selenium-{request.node.name}.png")


def test_dashboard(driver):
    """Ensures the dashboard can be connected to."""

    driver, wait, url = driver

    # TODO: More testing
    wait.until(EC.presence_of_element_located((By.ID, "root")))
