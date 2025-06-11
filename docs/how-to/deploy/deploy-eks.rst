Deploy to EKS
==============

This guide shows how to deploy Charmed MLflow on `AWS Elastic Kubernetes Service <https://aws.amazon.com/eks/>`_ (EKS). In this guide, you will create an AWS EKS cluster, connect Juju to it, and deploy the MLflow bundle.

Requirements
-------------

- An AWS account (`How to create an AWS account <https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-creating.html>`_).
- A machine that runs Ubuntu 22.04 or a newer version.

Create EKS cluster
-------------------
See the `EKS creation guide <https://discourse.charmhub.io/t/create-an-eks-cluster-for-use-with-an-mlops-platform/10983>`_ for how to create an EKS cluster where Charmed MLflow will be deployed.

Setup Juju
----------

Set up your local ``juju`` to talk to the remote Kubernetes (K8s) cloud. First, install ``juju``:

.. code-block:: bash

   sudo snap install juju --channel=3.6/stable

Connect Juju to Kubernetes:

.. code-block:: bash

   juju add-k8s kubeflow

Create a controller:

.. code-block:: bash

   juju bootstrap --no-gui kubeflow kubeflow-controller

.. note:: You can use whatever controller name you like here, we chose ``kubeflow-controller``.

Add a Juju model:

.. code-block:: bash

   juju add-model kubeflow

.. note:: You must choose the name ``kubeflow`` if you plan to connect MLflow to Kubeflow. Otherwise you can choose any name.

Deploy MLflow bundle
---------------------
Deploy the MLflow bundle with the following command:

.. code-block:: bash

    juju deploy mlflow --channel=2.22/stable --trust

This deploys the stable version of MLflow with `MinIO <https://min.io/>`_ as the object storage and `MySQL` as the metadata store.

Once the deployment is completed, you will see a message such as the following:

.. code-block:: bash
   
   Deploy of bundle completed.

You can use the following command to check the status of all model components:

.. code-block:: bash

   juju status

The deployment is ready when all the applications and units in the bundle are in active status. 
You can also use the ``watch`` option to continuously monitor the statuses:

.. code-block:: bash

   juju status --watch 5s

During the deployment process, some of the components statuses may momentarily change to blocked or error state. 
This is an expected behaviour, and these statuses should resolve by themselves as the bundle configures.

Access your deployment
-----------------------

To access your Charmed MLflow deployment, navigate to the following URL:

.. code-block:: bash

   http://localhost:31380/

This will take you to the MLflow User Interface (UI).

.. note:: 
   By default, Charmed MLflow creates a `NodePort <https://kubernetes.io/docs/concepts/services-networking/service/#type-nodeport>`_ on port 31380, which you can use to access the MLflow UI.
