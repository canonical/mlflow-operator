"""Charms dependencies for tests."""

from charmed_kubeflow_chisme.testing import CharmSpec

# for Istio in sidecar mode:
ISTIO_GATEWAY = CharmSpec(
    charm="istio-gateway", channel="latest/edge", config={"kind": "ingress"}, trust=True
)
ISTIO_PILOT = CharmSpec(
    charm="istio-pilot",
    channel="latest/edge",
    config={"default-gateway": "test-gateway"},
    trust=True,
)

# for Istio in ambient mode:
ISTIO_BEACON_K8S = CharmSpec(charm="istio-beacon-k8s", channel="2/edge", trust=True)
ISTIO_K8S = CharmSpec(charm="istio-k8s", channel="2/edge", trust=True, config={"platform": ""})

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
    trust=True,
)
MYSQL_K8S = CharmSpec(
    charm="mysql-k8s", channel="8.0/stable", config={"profile": "testing"}, trust=True
)
RESOURCE_DISPATCHER = CharmSpec(charm="resource-dispatcher", channel="latest/edge", trust=True)
