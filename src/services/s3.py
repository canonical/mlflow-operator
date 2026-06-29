"""Wrapper for accessing and creating S3 Buckets."""

import os
import tempfile
from typing import List, Optional, Union

import boto3
import botocore.client
from botocore.config import Config
from botocore.exceptions import ClientError

CONNECT_TIMEOUT = 10
READ_TIMEOUT = 10


class S3BucketWrapper:
    """Wrapper for accessing and creating S3 Buckets."""

    def __init__(
        self,
        access_key: str,
        secret_access_key: str,
        s3_service: str,
        s3_port: Union[str, int],
        secure: bool = False,
        region: str = "",
        tls_ca_chain: Optional[List[str]] = None,
    ):
        """Initialize S3 Bucket Wrapper.

        access_key - S3 access key ID
        secret_access_key - S3 secret access key
        s3_service - S3 service URL that can include namespace
        s3_port - S3 service port
        secure - whether to use HTTPS (default: False)
        region - S3 region, used for bucket creation location constraint (default: "")
        tls_ca_chain - list of PEM CA certificates for TLS verification (default: None)
        """

        self.access_key: str = access_key
        self.secret_access_key: str = secret_access_key
        self.s3_service: str = s3_service
        self.s3_port: str = str(s3_port)
        self.secure: bool = secure
        self.region: str = region

        self._ca_file: Optional[str] = None
        if tls_ca_chain:
            ca_chain_pem = "\n".join(tls_ca_chain)
            with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".pem") as fd:
                fd.write(ca_chain_pem)
                self._ca_file = fd.name

        self._client: botocore.client.BaseClient = None

    def __del__(self):
        """Best-effort cleanup of the temporary CA file if one was created."""
        ca_file = getattr(self, "_ca_file", None)
        if not ca_file:
            return
        try:
            os.unlink(ca_file)
        except FileNotFoundError:
            pass
        except OSError:
            pass

    def create_bucket(self, bucket_name):
        """Create a bucket."""
        kwargs = {"Bucket": bucket_name}
        # AWS S3 requires CreateBucketConfiguration for all regions except us-east-1, and
        # rejects it for us-east-1. Use an empty region for non-AWS endpoints. See:
        # https://docs.aws.amazon.com/boto3/latest/reference/services/s3/client/create_bucket.html#create-bucket
        if self.region and self.region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
        try:
            self.client.create_bucket(**kwargs)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            # Treat already-existing buckets as success
            if error_code in ("BucketAlreadyOwnedByYou", "BucketAlreadyExists"):
                return
            raise

    def delete_bucket(self, bucket_name):
        """Delete a bucket."""
        self.client.delete_bucket(Bucket=bucket_name)

    def bucket_exists(self, bucket_name: str) -> bool:
        """Check if a bucket exists and is accessible.

        Returns True if the bucket exists and is reachable, False if it does not exist.
        Raises ClientError for other failures (e.g., auth, connectivity).
        """
        try:
            self.client.head_bucket(Bucket=bucket_name)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code")
            if error_code in ("404", "NoSuchBucket", "NotFound"):
                return False
            # Re-raise for other errors (e.g., 403, connection issues)
            raise

    @property
    def client(self) -> botocore.client.BaseClient:
        """Returns an open boto3 client, creating and caching one if needed. Singleton pattern."""
        if self._client:
            return self._client
        else:
            self._client = boto3.client(
                "s3",
                endpoint_url=self.s3_url,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_access_key,
                config=Config(connect_timeout=CONNECT_TIMEOUT, read_timeout=READ_TIMEOUT),
                verify=self._ca_file,
            )
            return self._client

    @property
    def s3_url(self):
        """Returns the S3 url."""
        scheme = "https" if self.secure else "http"
        return f"{scheme}://{self.s3_service}:{self.s3_port}"
