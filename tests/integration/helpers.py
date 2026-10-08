# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.

"""Shared helpers for the integration tests."""

import logging
import subprocess
from random import choices
from string import ascii_lowercase

from tenacity import Retrying, retry_if_exception_type, stop_after_delay, wait_fixed

logger = logging.getLogger(__name__)


class CurlPod:
    """A long-running pod issuing curl requests from within a given namespace.

    Use it as a context manager, so the pod is created on entry and deleted on exit.
    """

    IMAGE = "curlimages/curl:8.8.0"

    def __init__(self, namespace: str):
        self.namespace = namespace
        self.name = f"curl-{''.join(choices(ascii_lowercase, k=6))}"

    def __enter__(self) -> "CurlPod":
        self.create()
        return self

    def __exit__(self, *exc) -> None:
        self.delete()

    def create(self) -> None:
        """Create the pod and wait until it is ready, deleting it if it never gets ready."""
        # a fresh namespace's default service account may not exist yet, rejecting the pod:
        for attempt in Retrying(
            stop=stop_after_delay(60),
            wait=wait_fixed(2),
            retry=retry_if_exception_type(subprocess.CalledProcessError),
            reraise=True,
        ):
            with attempt:
                self._kubectl(
                    "run",
                    self.name,
                    f"--image={self.IMAGE}",
                    "--restart=Never",
                    "--command",
                    "--",
                    "sleep",
                    "86400",
                )
        try:
            self._kubectl("wait", f"pod/{self.name}", "--for=condition=Ready", "--timeout=180s")
        except subprocess.CalledProcessError:
            self.delete()
            raise

    def delete(self) -> None:
        """Delete the pod, tolerating it being already gone."""
        self._kubectl(
            "delete", "pod", self.name, "--ignore-not-found", "--wait=false", check=False
        )

    def curl(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        """Run curl in the pod; `kubectl exec` propagates curl's own exit code."""
        result = self._kubectl("exec", self.name, "--", "curl", *args, check=False)
        logger.info(
            "curl from pod %s/%s: exit_code=%s stdout=%r stderr=%r",
            self.namespace,
            self.name,
            result.returncode,
            result.stdout,
            result.stderr,
        )
        if check:
            result.check_returncode()
        return result

    def _kubectl(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["kubectl", "-n", self.namespace, *args],
            check=check,
            capture_output=True,
            text=True,
        )
