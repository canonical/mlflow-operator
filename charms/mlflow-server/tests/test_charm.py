import pytest

from ops.model import BlockedStatus, WaitingStatus, ActiveStatus
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


def test_install_with_all_inputs(harness):
    harness.set_leader(True)
    harness.add_oci_resource(
        "oci-image",
        {
            "registrypath": "ci-test",
            "username": "",
            "password": "",
        },
    )

    # mysql relation data
    mysql_data = {
        "database": "database",
        "host": "host",
        "root_password": "root_password",
        "port": "port",
    }
    rel_id = harness.add_relation("db", "mysql_app")
    harness.add_relation_unit(rel_id, "mysql_app/0")
    harness.update_relation_data(rel_id, "mysql_app/0", mysql_data)

    # object storage
    os_data = {
        "_supported_versions": "- v1",
        "data": yaml.dump(
            {
                "access-key": "minio-access-key",
                "namespace": "namespace",
                "port": 1234,
                "secret-key": "minio-super-secret-key",
                "secure": True,
                "service": "service",
            }
        ),
    }
    os_rel_id = harness.add_relation("object-storage", "storage-provider")
    harness.add_relation_unit(os_rel_id, "storage-provider/0")
    harness.update_relation_data(os_rel_id, "storage-provider", os_data)

    # ingress
    ingress_relation_name = "ingress"
    relation_version_data = {"_supported_versions": "- v1"}
    ingress_rel_id = harness.add_relation(
        ingress_relation_name, f"{ingress_relation_name}-subscriber"
    )
    harness.add_relation_unit(ingress_rel_id, f"{ingress_relation_name}-subscriber/0")
    harness.update_relation_data(
        ingress_rel_id, f"{ingress_relation_name}-subscriber", relation_version_data
    )

    harness.begin_with_initial_hooks()

    pod_spec = harness.get_pod_spec()
    yaml.safe_dump(pod_spec)
    assert harness.charm.model.unit.status == ActiveStatus()
