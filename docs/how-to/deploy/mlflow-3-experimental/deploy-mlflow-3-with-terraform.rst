Deploy MLflow 3 with Terraform
==============================

This guide describes how to deploy Charmed MLflow 3 (experimental) using `Terraform <https://www.terraform.io/>`__, via the Charmed Kubeflow Terraform solution.

It uses the Terraform solution from the ``main`` branch of the `charmed-kubeflow-solutions <https://github.com/canonical/charmed-kubeflow-solutions>`_ repository, deployed from the ``latest/edge`` channels. For MLflow, ``latest/edge`` currently provides MLflow 3, which is the only way to deploy it at the moment.

.. warning::
    MLflow 3 support is **experimental** and is not intended for production workloads. The ``main`` branch of ``charmed-kubeflow-solutions`` and the ``latest/edge`` channels are under active development and may change at any time.

Requirements
-------------

* A Kubernetes cluster of version 1.32, 1.33 or 1.34.
* The `Juju <https://juju.is/>`_ client installed, with a controller bootstrapped on your Kubernetes cloud. The Terraform Juju provider operates on the controller your Juju client is currently connected to.
* `Terraform <https://developer.hashicorp.com/terraform/install>`__ version 1.6 or later.
* `Git <https://git-scm.com/>`_.

Deploy MLflow 3
----------------

Download the solution source by cloning the repository:

.. code-block:: bash

    git clone https://github.com/canonical/charmed-kubeflow-solutions.git

Move into the Terraform solution directory:

.. code-block:: bash

    cd charmed-kubeflow-solutions/terraform/products/kubeflow

Initialise Terraform to download the provider and modules:

.. code-block:: bash

    terraform init

Deploy the solution, enabling MLflow and selecting the ``latest/edge`` channels:

.. code-block:: bash

    terraform apply -var 'enable_mlflow=true' -var 'release=latest'

The ``release=latest`` variable, together with the default ``risk=edge``, selects the ``latest/edge`` channels. For MLflow, ``latest/edge`` currently ships MLflow 3, whereas pinned releases (such as ``release=1.11``) ship MLflow 2.

.. note::
    This command does not deploy the full Charmed Kubeflow platform. With only MLflow enabled, the solution brings up MLflow together with:

    * The components MLflow needs:

      * its backend store (``mysql-k8s``)
      * its artifact store (``minio``)
      * the resource dispatcher
      * KServe

    * The base layer that the Kubeflow product always includes:

      * a service mesh (Istio)
      * authentication
      * Kubeflow core (Dashboard, Profiles, and related controllers)

    Optional components such as Kubeflow Pipelines and Notebooks are not deployed.

Terraform creates the ``kubeflow`` model and deploys the applications. You can watch the progress and wait for them to become ``active`` with:

.. code-block:: bash

    juju status --watch 5s
