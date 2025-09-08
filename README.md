# Kubeflow + MLflow on Juju with Microk8s

- [Kubeflow + MLflow on Juju with Microk8s](#kubeflow--mlflow-on-juju-with-microk8s)
  - [Get Started](#get-started)
    - [Deploy Standalone MLflow Server](#deploy-standalone-mlflow-server)
    - [Deploy MLflow Server with Kubeflow](#deploy-mlflow-server-with-kubeflow)
  - [Run an Example Model with Kubeflow](#run-an-example-model-with-kubeflow)
  - [Access Artifacts](#access-artifacts)
    - [Get MinIO Key and Secret](#get-minio-key-and-secret)
    - [MinIO Client](#minio-client)
    - [Boto3](#boto3)

## Get Started

### Deploy Standalone MLflow Server
You can follow [these always up-to-date docs](https://documentation.ubuntu.com/charmed-mlflow/en/latest/tutorial/mlflow/) for a step-by-step procedure.

### Deploy MLflow Server with Kubeflow
You can follow [these always up-to-date docs](https://documentation.ubuntu.com/charmed-mlflow/en/latest/tutorial/mlflow-kubeflow/) for a step-by-step procedure.

## Run an Example Model with Kubeflow
Our docs also provide [instructions](https://documentation.ubuntu.com/charmed-mlflow/en/latest/tutorial/mlflow-kubeflow/#run-mlflow-examples) on how to run MLflow examples.

## Access Artifacts
Based on the setup in the Get Started section, artifacts will be stored in MinIO. You are able to access the artifacts using the MinIO client or Boto3 with Python.

### Get MinIO Access Key and Secret Access Key
Run the MLflow action to retrieve MinIO credentials:
```shell
juju run-action mlflow-server/0 get-minio-credentials --wait

# Expected result
unit-mlflow-server-0:
  UnitId: mlflow-server/0
  id: "2"
  results:
    access-key: minio
    secret-access-key: P7B9CT4YX39QDF22LOG83EU9PA2UOA
  status: completed
  timing:
    completed: 2023-10-05 06:15:16 +0000 UTC
    enqueued: 2023-10-05 06:15:15 +0000 UTC
    started: 2023-10-05 06:15:15 +0000 UTC
```

### MinIO client

Install the MinIO client following the [official guide](https://docs.min.io/docs/minio-client-quickstart-guide.html).

Set alias for the minio
```
mc alias set <alias> http://`juju status --format yaml | yq .applications.minio.units.minio/*.address`:9000 $AWS_ACCESS_KEY_ID $AWS_SECRET_ACCESS_KEY
```

To list the content in the default MLflow bucket:
```
mc ls <alias>/mlflow
```

To read the content of a specific file:
```
mc cat <alias>/<path to file>
```

### Boto3
As an alternative, you can use Boto3 to interact with MinIO in Python:
```python
import boto3
minio = boto3.client(
        "s3",
        endpoint_url=os.getenv("MLFLOW_S3_ENDPOINT_URL"),
        config=boto3.session.Config(signature_version="s3v4"),
    )
```
Note: If you are accessing the bucket outside of a Kubeflow notebook server, replace the OS environment variable with the MinIO unit's IP with :9000 at the end. Run this in the terminal to get the IP:
```shell
echo http://`juju status --format yaml | yq .applications.minio.units.minio/*.address`:9000
```
<br>

To list files in the default bucket mlflow:
```python
response = minio.list_objects_v2(Bucket="mlflow")
files = response.get("Contents")
for file in files:
    print(f"file_name: {file['Key']}, size: {file['Size']}")
```

To download a specific file:
```python
minio.download_file(default_bucket_name,'<minio file path>', '<notebook server file path>')
```
