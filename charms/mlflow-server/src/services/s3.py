"""Wrapper for basic accessing and validating of S3 Buckets."""

import re
from typing import Union

import boto3
import botocore.client
import botocore.exceptions


class S3BucketWrapper:
    """Wrapper for basic accessing and validating of S3 Buckets."""

    def __init__(
        self, access_key: str, secret_access_key: str, s3_service: str, s3_port: Union[str, int]
    ):
        self.access_key: str = access_key
        self.secret_access_key: str = secret_access_key
        self.s3_service: str = s3_service
        self.s3_port: str = str(s3_port)

        self._client: botocore.client.BaseClient = None

    def check_if_bucket_accessible(self, bucket_name):
        """Checks if a bucket exists and is accessible, returning True if both are satisfied.

        Will return False if we encounter a botocore.exceptions.ClientError, which could be
        due to the bucket not existing, the client session not having permission to access the
        bucket, or some other error with the client.
        """
        try:
            self.client.head_bucket(Bucket=bucket_name)
            return True
        except botocore.exceptions.ClientError:
            return False

    def create_bucket_if_missing(self, bucket_name):
        """Creates the bucket bucket_name if it does not exist, raising an error if it cannot.

        This method tries to access the bucket, assuming that if it is unaccessible that it does
        not exist (this is a required assumption as unaccessible buckets look the same as those
        that do not exist).  If inaccessible, we try to create_bucket and do not catch any
        exceptions that result from the call.
        """
        if self.check_if_bucket_accessible(bucket_name=bucket_name):
            return

        self.create_bucket(bucket_name=bucket_name)

    def create_bucket(self, bucket_name):
        """Create a bucket via the client."""
        self.client.create_bucket(Bucket=bucket_name)

    @property
    def client(self) -> botocore.client.BaseClient:
        """Returns an open boto3 client, creating and caching one if needed."""
        if self._client:
            return self._client
        else:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.s3_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_access_key,
            )
            return self._client

    @property
    def s3_url(self):
        """Returns the S3 url."""
        return f"http://{self.s3_service}:{self.s3_port}"


def validate_s3_bucket_name(name):
    """Returns True if name is a valid S3 bucket name, else False."""
    # regex from https://stackoverflow.com/a/50484916/5394584
    if re.match(
        r"(?=^.{3,63}$)(?!^(\d+\.)+\d+$)(^(([a-z0-9]|[a-z0-9][a-z0-9\-]*[a-z0-9])\.)*([a-z0-9]|[a-z0-9][a-z0-9\-]*[a-z0-9])$)",  # noqa:
        name,
    ):
        return True
    else:
        return False
