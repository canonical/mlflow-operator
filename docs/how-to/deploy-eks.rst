Deploy Charmed MLflow to EKS
============================

+------------+---------+
| Component  | Version |
+============+=========+
| MLflow     | 2       |
+------------+---------+

This guide shows how to deploy Charmed MLflow on `AWS Elastic Kubernetes Service <https://aws.amazon.com/eks/>`_ (EKS). In this guide, we will create an AWS EKS cluster, connect Juju to it, and deploy the MLflow bundle.

Prerequisites:
--------------
We assume the following:

- Your machine runs Ubuntu 22.04 or later
- You have an AWS account (`How to create an AWS account <https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-creating.html>`_)

Create EKS cluster
-------------------
See the `EKS creation guide <https://charmed-kubeflow.io/docs/create-eks-cluster-for-mlops>`_ for how to do that.

Setup Juju
----------

Set up your local ``juju`` to talk to the remote Kubernetes (K8s) cloud. First, install ``juju``:

.. code-block:: bash

   sudo snap install juju --classic

Connect Juju to Kubernetes:

.. code-block:: bash

   juju add-k8s kubeflow

.. note:: You must choose the name ``kubeflow`` if you plan to connect MLflow to Kubeflow. Otherwise you can choose any name.

Create a controller:

.. code-block:: bash

   juju bootstrap --no-gui kubeflow kubeflow-controller

.. note:: You can use whatever controller name you like here, we chose ``kubeflow-controller``.

Add a juju model:

.. code-block:: bash

   juju add-model kubeflow

.. note:: You must choose the name ``kubeflow`` if you plan to connect MLflow to Kubeflow. Otherwise you can choose any name.

Deploy MLflow bundle
---------------------
Deploy the MLflow bundle with the following command:

.. code-block:: bash

    juju deploy mlflow --channel=2.1/edge --trust

Wait until all charms are in the active state. You can check the state of the charms with the command:

.. code-block:: bash

    juju status --watch 5s --relations
