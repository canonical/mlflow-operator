# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from base64 import b64decode
from contextlib import nullcontext as does_not_raise

import pytest
import yaml
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

from charm import CheckFailedError, Operator, validate_s3_bucket_name


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

    assert harness.charm.model.unit.status == WaitingStatus("Waiting for mysql relation data")


@pytest.mark.parametrize(
    "name,context_raised",
    [
        # Note, this is a non-exhaustive list
        ("some-valid-name", does_not_raise()),
        ("0123456789", does_not_raise()),
        ("01", pytest.raises(CheckFailedError)),  # name too short
        ("x" * 64, pytest.raises(CheckFailedError)),  # name too long
        ("some_invalid_name", pytest.raises(CheckFailedError)),  # name has '_'
        ("some;invalid;name" * 64, pytest.raises(CheckFailedError)),  # name has special characters
        ("Some-Invalid-Name", pytest.raises(CheckFailedError)),  # name has capitals
    ],
)
def test_validate_s3_bucket_name(name, context_raised):
    with context_raised as err:
        assert name == validate_s3_bucket_name(name)
    if isinstance(err, Exception):
        error_message = "Invalid value for config default_artifact_root"
        assert error_message in str(err)
        assert err.status_type == BlockedStatus


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
        "root_password": "lorem-ipsum",
        "port": "port",
    }
    rel_id = harness.add_relation("db", "mysql_app")
    harness.add_relation_unit(rel_id, "mysql_app/0")
    harness.update_relation_data(rel_id, "mysql_app/0", mysql_data)

    default_artifact_root = "not-a-typical-bucket-name"
    harness.update_config({"default_artifact_root": default_artifact_root})

    # object storage
    os_data_dict = {
        "access-key": "minio-access-key",
        "namespace": "namespace",
        "port": 1234,
        "secret-key": "minio-super-secret-key",
        "secure": True,
        "service": "service",
    }
    os_data = {"_supported_versions": "- v1", "data": yaml.dump(os_data_dict)}
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

    # pod defaults relations setup
    pod_defaults_rel_name = "pod-defaults"
    pod_defaults_rel_id = harness.add_relation(
        "pod-defaults", f"{pod_defaults_rel_name}-subscriber"
    )
    harness.add_relation_unit(pod_defaults_rel_id, f"{pod_defaults_rel_name}-subscriber/0")

    harness.begin_with_initial_hooks()

    pod_spec = harness.get_pod_spec()
    yaml.safe_dump(pod_spec)
    assert harness.charm.model.unit.status == ActiveStatus()

    charm_name = harness.model.app.name
    secrets = pod_spec[0]["kubernetesResources"]["secrets"]
    env_config = pod_spec[0]["containers"][0]["envConfig"]
    secrets_dict = {s["name"]: s for s in secrets}

    assert (
        env_config["db-secret"]["secret"]["name"]
        == secrets_dict[f"{charm_name}-db-secret"]["name"]
    )
    assert (
        b64decode(secrets_dict[f"{charm_name}-db-secret"]["data"]["DB_ROOT_PASSWORD"]).decode(
            "utf-8"
        )
        == "lorem-ipsum"
    )
    assert b64decode(
        secrets_dict[f"{charm_name}-db-secret"]["data"]["MLFLOW_TRACKING_URI"]
    ).decode("utf-8") == "mysql+pymysql://{}:{}@{}:{}/{}".format(
        "root",
        mysql_data["root_password"],
        mysql_data["host"],
        mysql_data["port"],
        mysql_data["database"],
    )

    # Check minio credentials
    assert (
        env_config["aws-secret"]["secret"]["name"]
        == secrets_dict[f"{charm_name}-minio-secret"]["name"]
    )
    assert (
        b64decode(secrets_dict[f"{charm_name}-minio-secret"]["data"]["AWS_ACCESS_KEY_ID"]).decode(
            "utf-8"
        )
        == os_data_dict["access-key"]
    )
    assert (
        b64decode(
            secrets_dict[f"{charm_name}-minio-secret"]["data"]["AWS_SECRET_ACCESS_KEY"]
        ).decode("utf-8")
        == os_data_dict["secret-key"]
    )

    # Spot check for seldon init-container credentials
    assert (
        b64decode(
            secrets_dict[f"{charm_name}-seldon-init-container-s3-credentials"]["data"][
                "RCLONE_CONFIG_S3_ACCESS_KEY_ID"
            ]
        ).decode("utf-8")
        == os_data_dict["access-key"]
    )
    assert len(secrets_dict[f"{charm_name}-seldon-init-container-s3-credentials"]["data"]) == 6

    # Confirm default_artifact_root config
    args = pod_spec[0]["containers"][0]["args"]
    default_artifact_root_arg_index = args.index("--default-artifact-root")
    expected_bucket_name = f"s3://{default_artifact_root}/"
    actual_bucket_name = args[default_artifact_root_arg_index + 1]
    assert actual_bucket_name == expected_bucket_name, (
        f"pod_spec container args have unexpected default-artifact-root."
        f"  Expected {expected_bucket_name}, found {actual_bucket_name}"
    )

    # test correct data structure is sent to admission webhook
    mlflow_pod_defaults_data = {
        key.name: value
        for key, value in harness.model.get_relation(
            pod_defaults_rel_name, pod_defaults_rel_id
        ).data.items()
        if "mlflow-server" in key.name
    }
    mlflow_pod_defaults_minio_data = json.loads(
        mlflow_pod_defaults_data[charm_name]["pod-defaults"]
    )["minio"]["env"]

    assert mlflow_pod_defaults_minio_data["AWS_ACCESS_KEY_ID"] == os_data_dict["access-key"]
    assert mlflow_pod_defaults_minio_data["AWS_SECRET_ACCESS_KEY"] == os_data_dict["secret-key"]
    assert (
        mlflow_pod_defaults_minio_data["MLFLOW_S3_ENDPOINT_URL"]
        == f"http://{os_data_dict['service']}.{os_data_dict['namespace']}:{os_data_dict['port']}"
    )
    assert (
        mlflow_pod_defaults_minio_data["MLFLOW_TRACKING_URI"]
        == f"http://{harness.model.app.name}.{harness.model.name}.svc.cluster.local:{harness.charm.config['mlflow_port']}"
    )
