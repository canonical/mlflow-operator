"""Charms dependencies for tests."""

from charmed_kubeflow_chisme.testing import CharmSpec

# for Istio in sidecar mode only:
ISTIO_GATEWAY = CharmSpec(
    charm="istio-gateway", channel="1.28/edge", config={"kind": "ingress"}, trust=True
)
ISTIO_PILOT = CharmSpec(
    charm="istio-pilot",
    channel="1.28/edge",
    config={"default-gateway": "test-gateway"},
    trust=True,
)

METACONTROLLER_OPERATOR = CharmSpec(
    charm="metacontroller-operator", channel="4.11/edge", trust=True
)
MINIO = CharmSpec(
    charm="minio",
    channel="1.10/edge",
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
RESOURCE_DISPATCHER = CharmSpec(charm="resource-dispatcher", channel="2.0/edge", trust=True)
KUBEFLOW_PROFILES = CharmSpec(
    charm="kubeflow-profiles",
    channel="latest/edge",
    config={
        "service-mesh-mode": "istio-ambient",
        "istio-gateway-service-account": "istio-ingress-k8s-istio",
    },
    trust=True,
)
