from contextlib import nullcontext as does_not_raise

import botocore.exceptions
import pytest
from pytest_lazyfixture import lazy_fixture

from services.s3 import S3BucketWrapper, validate_s3_bucket_name


@pytest.mark.parametrize(
    "name,returned",
    [
        # Note, this is a non-exhaustive list
        ("some-valid-name", True),
        ("0123456789", True),
        ("01", False),  # name too short
        ("x" * 64, False),  # name too long
        ("some_invalid_name", False),  # name has '_'
        ("some;invalid;name" * 64, False),  # name has special characters
        ("Some-Invalid-Name", False),  # name has capitals
    ],
)
def test_validate_s3_bucket_name(name, returned):
    assert returned == validate_s3_bucket_name(name)


# autouse to prevent calling out to an external service
@pytest.fixture(autouse=True)
def mocked_boto3_client(mocker):
    boto3_client_instance = mocker.MagicMock()
    boto3_client_class = mocker.patch("boto3.client")
    boto3_client_class.return_value = boto3_client_instance
    yield boto3_client_instance


@pytest.fixture(scope="function")
def client_bucket_accessible(mocked_boto3_client):
    mocked_boto3_client.head_bucket.return_value = True
    yield mocked_boto3_client


@pytest.fixture(scope="function")
def client_accessible_emitting_ClientError(mocked_boto3_client):  # noqa: N802
    mocked_boto3_client.head_bucket.side_effect = botocore.exceptions.ClientError({}, "test")
    yield mocked_boto3_client


@pytest.fixture(scope="function")
def client_accessible_emitting_unknown_exception(mocked_boto3_client):
    mocked_boto3_client.head_bucket.side_effect = Exception("some unexpected error")
    yield mocked_boto3_client


@pytest.fixture(scope="function")
def s3_wrapper_empty():
    wrapper = S3BucketWrapper(
        access_key="",
        secret_access_key="",
        s3_service="",
        s3_port="",
    )
    return wrapper


@pytest.mark.parametrize(
    "expected_returned,mocked_client,context_raised",
    [
        (True, lazy_fixture("client_bucket_accessible"), does_not_raise()),
        (
            False,
            lazy_fixture("client_accessible_emitting_ClientError"),
            does_not_raise(),
        ),  # A handled error, returning False
        (
            None,
            lazy_fixture("client_accessible_emitting_unknown_exception"),
            pytest.raises(Exception),
        ),
    ],
)
def test_check_if_bucket_accessible(
    expected_returned, mocked_client, context_raised, s3_wrapper_empty
):
    with context_raised:
        s3_wrapper_empty._client = mocked_client

        bucket_name = "some_bucket"
        returned = s3_wrapper_empty.check_if_bucket_accessible(bucket_name)
        assert returned == expected_returned

        s3_wrapper_empty.client.head_bucket.assert_called_with(Bucket=bucket_name)


@pytest.mark.parametrize(
    "is_bucket_accessible,",
    [
        (True,),
        (False,),
    ],
)
def test_create_bucket_if_not_exists(
    is_bucket_accessible, mocked_boto3_client, mocker, s3_wrapper_empty
):
    mocked_check_if_bucket_accessible = mocker.patch(
        "services.s3.S3BucketWrapper.check_if_bucket_accessible"
    )
    mocked_check_if_bucket_accessible.return_value = is_bucket_accessible

    bucket_name = "some_bucket"
    s3_wrapper_empty.create_bucket_if_missing(bucket_name)

    mocked_check_if_bucket_accessible.assert_called_with(bucket_name=bucket_name)

    if is_bucket_accessible:
        # Bucket already existed, so we do not create
        mocked_boto3_client.create_bucket.assert_not_called()
    else:
        # Bucket not accessible, so we try to create
        mocked_boto3_client.create_bucket.assert_called_once_with(Bucket=bucket_name)
