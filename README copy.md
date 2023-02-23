# mlflow

## Description

A charm which provides a Kubernetes installation of the [MLflow](https://mlflow.org/) server.

Optionally, it knows how to interface with the Kubeflow charms to configure Kubeflow notebook clients with access to the MLflow server.

## Usage

TODO: Provide high-level usage, such as required config or relations


## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Just `run_tests`:

    ./run_tests
