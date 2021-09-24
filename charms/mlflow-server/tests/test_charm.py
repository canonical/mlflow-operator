import pytest

from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness
import yaml

from charm import Operator


@pytest.fixture
def harness():
    return Harness(Operator)


def test_not_leader(harness):
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.model.unit.status, WaitingStatus)


def test_missing_image(harness):
    harness.set_leader(True)
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.model.unit.status, BlockedStatus)


def test_main_no_relation(harness):
    harness.set_leader(True)
    harness.add_oci_resource(
        "oci-image",
        {
            "registrypath": "ci-test",
            "username": "",
            "password": "",
        },
    )
    harness.begin_with_initial_hooks()
    pod_spec = harness.get_pod_spec()

    # confirm that we can serialize the pod spec
    yaml.safe_dump(pod_spec)

    assert harness.charm.model.unit.status == WaitingStatus(
        "Waiting for mysql relation data"
    )


def test_with_v2_ingress(harness):
    harness.set_leader(True)
    harness.add_oci_resource(
        "oci-image",
        {
            "registrypath": "",
            "username": "",
            "password": "",
        },
    )
    # Set up db
    rel_id = harness.add_relation("db", "app")
    harness.add_relation_unit(rel_id, "app/0")
    harness.update_relation_data(
        rel_id,
        "app/0",
        {"database": "foobar", "root_password": "root", "host": "host", "port": "port"},
    )

    # Set up object-storage
    rel_id = harness.add_relation("object-storage", "app")
    harness.add_relation_unit(rel_id, "app/0")
    harness.update_relation_data(
        rel_id,
        "app",
        {
            "_supported_versions": "- v1",
            "data": yaml.dump(
                {
                    "access-key": "",
                    "port": 0,
                    "secret-key": "",
                    "secure": False,
                    "service": "",
                }
            ),
        },
    )

    # Set up ingress v1
    rel_id = harness.add_relation("ingress", "appv1")
    harness.add_relation_unit(rel_id, "appv1/0")
    harness.update_relation_data(
        rel_id,
        "appv1",
        {"_supported_versions": "- v1"},
    )

    # Set up ingress v2
    rel_id = harness.add_relation("ingress", "appv2")
    harness.add_relation_unit(rel_id, "appv2/0")
    harness.update_relation_data(
        rel_id,
        "appv2",
        {"_supported_versions": "- v2"},
    )

    harness.begin_with_initial_hooks()

    interfaces = harness.charm._get_interfaces()
    rel_data = interfaces["ingress"].get_data()
    rels = harness.model.relations["ingress"]

    assert rel_data == {
        (rels[0], harness.model.app): {
            "service": "mlflow-server",
            "port": 5000,
            "prefix": "/mlflow",
            "rewrite": "/",
        },
        (rels[1], harness.model.app): {
            "service": "mlflow-server",
            "port": 5000,
            "namespace": str(harness.model.name),
            "prefix": "/mlflow",
            "rewrite": "/",
        },
    }

    assert isinstance(harness.charm.model.unit.status, ActiveStatus)
