# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import json
from base64 import b64decode
from unittest.mock import MagicMock

import pytest
import yaml
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

from charm import CheckFailedError, Operator


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


def test_validate_default_s3_bucket__bucket_name_invalid(harness, mocker):
    mocked_validate_s3_bucket_name = mocker.patch("charm.validate_s3_bucket_name")
    mocked_validate_s3_bucket_name.return_value = False
    obj_storage = {}
    harness.begin()
    with pytest.raises(CheckFailedError) as raised:
        harness.charm._validate_default_s3_bucket(obj_storage=obj_storage)

    assert raised.value.status_type == BlockedStatus


@pytest.fixture()
def mocked_S3BucketWrapper(mocker):  # noqa: N802
    mocked_s3_bucket_wrapper_class = mocker.patch("charm.S3BucketWrapper")
    mocked_s3bucketwrapper_instance = MagicMock()
    mocked_s3_bucket_wrapper_class.return_value = mocked_s3bucketwrapper_instance
    return mocked_s3_bucket_wrapper_class, mocked_s3bucketwrapper_instance


@pytest.fixture()
def bucket_name_valid():
    return "some-valid-bucket-name"


@pytest.fixture()
def sample_object_storage():
    return {
        "access-key": "access-key-value",
        "secret-key": "secret-key-value",
        "service": "service-value",
        "port": "port-value",
    }


def test_validate_default_s3_bucket__bucket_is_accessible(
    harness, mocked_S3BucketWrapper, bucket_name_valid, sample_object_storage  # noqa: N803
):
    bucket_name = bucket_name_valid
    obj_storage = sample_object_storage

    # Mocking and setup
    mocked_s3bucketwrapper_class, mocked_s3bucketwrapper_instance = mocked_S3BucketWrapper
    mocked_s3bucketwrapper_instance.check_if_bucket_accessible.return_value = True

    harness.update_config(
        {
            "default_artifact_root": bucket_name,
        }
    )
    harness.begin()

    # Run the code
    returned_bucket_name = harness.charm._validate_default_s3_bucket(obj_storage=obj_storage)
    mocked_s3bucketwrapper_instance.check_if_bucket_accessible.assert_called_with(bucket_name)

    # Check that everything worked as expected
    assert returned_bucket_name == bucket_name


def test_validate_default_s3_bucket__missing__do_not_create_if_missing(
    harness, mocked_S3BucketWrapper, bucket_name_valid, sample_object_storage  # noqa: N803
):
    bucket_name = bucket_name_valid
    obj_storage = sample_object_storage

    # Mocking and setup
    mocked_s3bucketwrapper_class, mocked_s3bucketwrapper_instance = mocked_S3BucketWrapper
    mocked_s3bucketwrapper_instance.check_if_bucket_accessible.return_value = False

    harness.update_config(
        {
            "create_default_artifact_root_if_missing": False,
            "default_artifact_root": bucket_name,
        }
    )
    harness.begin()

    # Run the code
    with pytest.raises(CheckFailedError) as raised:
        harness.charm._validate_default_s3_bucket(obj_storage=obj_storage)

    # Check that everything worked as expected
    mocked_s3bucketwrapper_class.assert_called_with(
        access_key=obj_storage["access-key"],
        secret_access_key=obj_storage["secret-key"],
        s3_service=obj_storage["service"],
        s3_port=obj_storage["port"],
    )

    mocked_s3bucketwrapper_instance.check_if_bucket_accessible.assert_called_with(bucket_name)

    assert raised.value.status_type == BlockedStatus
    assert (
        "Set create_default_artifact_root_if_missing=True to automatically create"
        in raised.value.msg
    )


def test_validate_default_s3_bucket__missing__fail_to_create_if_missing(
    harness, mocked_S3BucketWrapper, bucket_name_valid, sample_object_storage  # noqa: N803
):
    bucket_name = bucket_name_valid
    obj_storage = sample_object_storage

    # Mocking and setup
    mocked_s3bucketwrapper_class, mocked_s3bucketwrapper_instance = mocked_S3BucketWrapper
    mocked_s3bucketwrapper_instance.check_if_bucket_accessible.return_value = False
    mocked_s3bucketwrapper_instance.create_bucket.side_effect = Exception("something went wrong")

    harness.update_config(
        {
            "create_default_artifact_root_if_missing": True,
            "default_artifact_root": bucket_name,
        }
    )
    harness.begin()

    # Run the code
    with pytest.raises(CheckFailedError) as raised:
        harness.charm._validate_default_s3_bucket(obj_storage=obj_storage)

    # Check that everything worked as expected
    mocked_s3bucketwrapper_class.assert_called_with(
        access_key=obj_storage["access-key"],
        secret_access_key=obj_storage["secret-key"],
        s3_service=obj_storage["service"],
        s3_port=obj_storage["port"],
    )

    mocked_s3bucketwrapper_instance.check_if_bucket_accessible.assert_called_with(bucket_name)
    mocked_s3bucketwrapper_instance.create_bucket.assert_called_with(bucket_name)

    assert raised.value.status_type == BlockedStatus
    assert "bucket not accessible or cannot be created" in raised.value.msg


def test_validate_default_s3_bucket__missing__create_if_missing(
    harness, mocked_S3BucketWrapper, bucket_name_valid, sample_object_storage  # noqa: N803
):
    bucket_name = bucket_name_valid
    obj_storage = sample_object_storage

    # Mocking and setup
    mocked_s3bucketwrapper_class, mocked_s3bucketwrapper_instance = mocked_S3BucketWrapper
    mocked_s3bucketwrapper_instance.check_if_bucket_accessible.return_value = False
    mocked_s3bucketwrapper_instance.create_bucket.return_value = bucket_name

    harness.update_config(
        {
            "create_default_artifact_root_if_missing": True,
            "default_artifact_root": bucket_name,
        }
    )
    harness.begin()

    # Run the code
    returned = harness.charm._validate_default_s3_bucket(obj_storage=obj_storage)

    # Check that everything worked as expected
    mocked_s3bucketwrapper_class.assert_called_with(
        access_key=obj_storage["access-key"],
        secret_access_key=obj_storage["secret-key"],
        s3_service=obj_storage["service"],
        s3_port=obj_storage["port"],
    )

    mocked_s3bucketwrapper_instance.check_if_bucket_accessible.assert_called_with(bucket_name)
    mocked_s3bucketwrapper_instance.create_bucket.assert_called_with(bucket_name)

    assert returned == bucket_name


def test_install_with_all_inputs(harness, mocker):
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

    # Mock away _validate_default_s3_bucket to avoid using boto3/creating clients
    mocked_validate_default_s3_bucket = mocker.patch("charm.Operator._validate_default_s3_bucket")
    bucket_name = harness._backend.config_get()["default_artifact_root"]
    mocked_validate_default_s3_bucket.return_value = bucket_name

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
