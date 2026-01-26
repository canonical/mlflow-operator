"""Charms dependencies for tests."""

from charmed_kubeflow_chisme.testing import CharmSpec

ISTIO_GATEWAY = CharmSpec(
    charm="istio-gateway", channel="latest/edge", config={"kind": "ingress"}, trust=True
)
ISTIO_PILOT = CharmSpec(
    charm="istio-pilot",
    channel="latest/edge",
    config={"default-gateway": "test-gateway"},
    trust=True,
)
METACONTROLLER_OPERATOR = CharmSpec(
    charm="metacontroller-operator", channel="latest/edge", trust=True
)
MINIO = CharmSpec(
    charm="minio",
    channel="latest/edge",
    config={
        "access-key": "minio",
        "secret-key": "minio123",
        "port": "9000",
    },
    trust=False,
)
MYSQL_K8S = CharmSpec(
    charm="mysql-k8s", channel="8.0/stable", config={"profile": "testing"}, trust=True
)
RESOURCE_DISPATCHER = CharmSpec(
    charm="resource-dispatcher",
    channel="latest/edge",
    trust=True,
    revision=417,  # TODO: Remove pinning once charm released to latest/edge
)
