# Kubeflow + MLflow on Juju with Microk8s

- [Kubeflow + MLflow on Juju with Microk8s](#kubeflow--mlflow-on-juju-with-microk8s)
  - [Get Started](#get-started)
    - [Deploy Standalone mlflow-server](#deploy-standalone-mlflow-server)
    - [Deploy mlflow-server with kubeflow](#deploy-mlflow-server-with-kubeflow)
  - [Run an Example Model with Kubeflow](#run-an-example-model-with-kubeflow)
  - [Access Artifacts](#access-artifacts)
    - [Get minio key and secret](#get-minio-key-and-secret)
    - [minio client](#minio-client)
    - [boto3](#boto3)

## Get Started

### Deploy Standalone mlflow-server
You can follow [this always up to date docs](https://documentation.ubuntu.com/charmed-mlflow/en/latest/tutorial/mlflow/) for step by step procedure. 

### Deploy mlflow-server with kubeflow
You can follow [this always up to date docs](https://documentation.ubuntu.com/charmed-mlflow/en/latest/tutorial/mlflow-kubeflow/) for step by step procedure. 

## Run an Example Model with Kubeflow
Our Docs also provide [instructions](https://documentation.ubuntu.com/charmed-mlflow/en/latest/tutorial/mlflow-kubeflow/#run-mlflow-examples) how to run MLflow examples.

## Access Artifacts
Based on the setup in the Get Started section, artifacts would be stored in minio. You could access the artifacts using the minio client or boto3 with python.

### Get MInIO access-key and secret-access-key
Run MLflow action to retriewe MinIO credentials 
```
juju run-action mlflow-server/0 get-minio-credentials --wait

# expect result
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

Install minio client following the [official guide](https://docs.min.io/docs/minio-client-quickstart-guide.html).

Set alias for the minio
```
mc alias set <alias> http://`juju status --format yaml | yq .applications.minio.units.minio/*.address`:9000 $AWS_ACCESS_KEY_ID $AWS_SECRET_ACCESS_KEY
```

To list content in the default mlflow bucket:
```
mc ls <alias>/mlflow
```

To read the content of a specific file:
```
mc cat <alias>/<path to file>
```

### boto3
As an alternative you can use boto3 to interact with MinIO.
```python
import boto3
minio = boto3.client(
        "s3",
        endpoint_url=os.getenv("MLFLOW_S3_ENDPOINT_URL"),
        config=boto3.session.Config(signature_version="s3v4"),
    )
```
Note: If you are accessing the bucket outside of a kubeflow notebook server, replace the os env with minio unit's ip with `:9000` at the end.
Run this in the terminal to get the ip: 
```shell
echo http://`juju status --format yaml | yq .applications.minio.units.minio/*.address`:9000
```
<br>

To list of files in the default bucket `mlflow`:
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