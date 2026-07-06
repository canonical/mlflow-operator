from contextlib import nullcontext as does_not_raise

import botocore.exceptions
import pytest
from pytest_lazy_fixtures import lf

from services.s3 import S3BucketWrapper


# autouse to prevent calling out to an external service
@pytest.fixture(autouse=True)
def mocked_boto3_client(mocker):
    boto3_client_instance = mocker.MagicMock()
    boto3_client_class = mocker.patch("boto3.client")
    boto3_client_class.return_value = boto3_client_instance
    yield boto3_client_instance


def _client_error(code):
    """Build a botocore ClientError carrying the given error code."""
    return botocore.exceptions.ClientError({"Error": {"Code": code, "Message": code}}, "operation")


@pytest.fixture(scope="function")
def client_bucket_exists(mocked_boto3_client):
    """head_bucket succeeds: the bucket exists and is accessible."""
    mocked_boto3_client.head_bucket.return_value = {}
    yield mocked_boto3_client


@pytest.fixture(scope="function")
def client_bucket_missing(mocked_boto3_client):
    """head_bucket raises a 404 ClientError: the bucket does not exist."""
    mocked_boto3_client.head_bucket.side_effect = _client_error("404")
    yield mocked_boto3_client


@pytest.fixture(scope="function")
def client_bucket_forbidden(mocked_boto3_client):
    """head_bucket raises a non-404 ClientError (e.g. 403): the error propagates."""
    mocked_boto3_client.head_bucket.side_effect = _client_error("403")
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
        # head_bucket succeeds -> the bucket exists
        (True, lf("client_bucket_exists"), does_not_raise()),
        # head_bucket returns a 404 -> the bucket does not exist (handled)
        (False, lf("client_bucket_missing"), does_not_raise()),
        # head_bucket returns any other error -> it propagates
        (None, lf("client_bucket_forbidden"), pytest.raises(botocore.exceptions.ClientError)),
    ],
)
def test_bucket_exists(expected_returned, mocked_client, context_raised, s3_wrapper_empty):
    with context_raised:
        s3_wrapper_empty._client = mocked_client

        bucket_name = "some-bucket"
        returned = s3_wrapper_empty.bucket_exists(bucket_name)
        assert returned == expected_returned

        s3_wrapper_empty.client.head_bucket.assert_called_with(Bucket=bucket_name)


@pytest.mark.parametrize(
    "region,expected_kwargs",
    [
        # Non-AWS / unspecified region: no CreateBucketConfiguration
        ("", {"Bucket": "some-bucket"}),
        # us-east-1 rejects CreateBucketConfiguration, so it must be omitted
        ("us-east-1", {"Bucket": "some-bucket"}),
        # Any other region requires the location constraint
        (
            "eu-west-1",
            {
                "Bucket": "some-bucket",
                "CreateBucketConfiguration": {"LocationConstraint": "eu-west-1"},
            },
        ),
    ],
)
def test_create_bucket_region_handling(region, expected_kwargs, mocked_boto3_client):
    wrapper = S3BucketWrapper(
        access_key="",
        secret_access_key="",
        s3_service="",
        s3_port="",
        region=region,
    )
    wrapper._client = mocked_boto3_client

    wrapper.create_bucket("some-bucket")

    mocked_boto3_client.create_bucket.assert_called_once_with(**expected_kwargs)


@pytest.mark.parametrize(
    "error_code,context_raised",
    [
        # An already-existing bucket is treated as success
        ("BucketAlreadyOwnedByYou", does_not_raise()),
        ("BucketAlreadyExists", does_not_raise()),
        # Any other error propagates
        ("AccessDenied", pytest.raises(botocore.exceptions.ClientError)),
    ],
)
def test_create_bucket_existing_bucket(
    error_code, context_raised, mocked_boto3_client, s3_wrapper_empty
):
    mocked_boto3_client.create_bucket.side_effect = botocore.exceptions.ClientError(
        {"Error": {"Code": error_code, "Message": error_code}}, "CreateBucket"
    )
    s3_wrapper_empty._client = mocked_boto3_client

    with context_raised:
        s3_wrapper_empty.create_bucket("some-bucket")

    mocked_boto3_client.create_bucket.assert_called_once_with(Bucket="some-bucket")


@pytest.mark.parametrize(
    "secure,expected_url",
    [
        (False, "http://minio.namespace:9000"),
        (True, "https://minio.namespace:9000"),
    ],
)
def test_s3_url(secure, expected_url):
    wrapper = S3BucketWrapper(
        access_key="",
        secret_access_key="",
        s3_service="minio.namespace",
        s3_port=9000,
        secure=secure,
    )
    assert wrapper.s3_url == expected_url
