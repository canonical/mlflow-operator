"""Charms dependencies for tests."""

from charmed_kubeflow_chisme.testing import CharmSpec

POSTGRESQL_K8S = CharmSpec(
    charm="postgresql-k8s", channel="14/stable", config={"profile": "testing"}, trust=True
)
S3_INTEGRATOR = CharmSpec(charm="s3-integrator", channel="2/stable", trust=False)

# for multi-tenancy, to configure workspace grants for users:
DATA_INTEGRATOR = CharmSpec(
    charm="data-integrator",
    channel="latest/edge",
    config={},
    trust=False,
)
