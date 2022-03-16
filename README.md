# Kubeflow + MLflow on Juju with Microk8s

## Get Started

Follow the [quick start guide](https://charmed-kubeflow.io/docs/quickstart) to deploy kubeflow on microk8s.


Deploy mlflow-server
```shell
juju deploy mlflow-server
juju deploy charmed-osm-mariadb-k8s mlflow-db
juju relate minio mlflow-server
juju relate istio-pilot mlflow-server
juju relate mlflow-db mlflow-server
juju relate mlflow-server admission-webhook
```

## MLFlow Dashboard

If you followed the instructions above, you could access MLflow dashboard by going to [http://10.64.140.43.nip.io/mlflow/#/](http://10.64.140.43.nip.io/mlflow/#/)
Otherwise, run `microk8s kubectl get services -A | grep "mlflow-server"`, and open the `mlflow` `ClusterIP` in the browser with `:5000` on the end.

![MLFlow Dashboard Screenshot](mlflow-dashboard.png "MLFlow Dashboard Screenshot")

## Run an Example Model
Temporary workaround for missing pod-defaults:
Run the following command to make a copy of pod defaults to user's namespace, which is `admin` following the guide.
`microk8s kubectl get poddefaults mlflow-server-minio -o yaml | sed 's/namespace: kubeflow/namespace: admin/' | microk8s kubectl create -f -`

Open [http://10.64.140.43.nip.io/](http://10.64.140.43.nip.io/) and log in with the username and password set in the quick start guide.

Create a new notebook server, taking care to specify the `mlflow-server-minio` configuration. This will ensure that the correct environment variables are set so that the MLflow SDK can connect to the MLflow server.

![config](config.png "Selecting the mlflow-minio configuration when launching a kubeflow notebook server")

Now open the notebook server and paste the following code into two cells:

```
!pip install sklearn mlflow boto3
```

This will install the required dependencies.

Paste the [example model code](./examples/elastic_net_wine_model.ipynb) in a separate cell.

Run both cells and observe that your model metrics are recorded in MLflow!

![screenshot](demo.png "Screenshot showing kubeflow notebook publishing to mlflow")
