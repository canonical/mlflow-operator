Deploy Charmed MLflow and Kubeflow to EKS
=========================================

+------------+---------+
| Component  | Version |
+============+=========+
| MLflow     | 2       |
+------------+---------+

This guide shows how to deploy Charmed MLflow alongside Kubeflow on `AWS Elastic Kubernetes Service <https://aws.amazon.com/eks/>`_ (EKS). In this guide, we will create an AWS EKS cluster, connect Juju to it, deploy the MLflow and Kubeflow bundles, and relate them to each other.

Prerequisites
-------------

We assume the following:

- Your machine runs Ubuntu 22.04 or later
- You have an AWS account (`How to create an AWS account <https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-creating.html>`_)

Deploy EKS cluster
-------------------

See our `EKS creation guide <https://charmed-kubeflow.io/docs/create-eks-cluster-for-mlops>`_ for a complete guide on how to do this. **Do not forget** to edit the ``instanceType`` field under ``managedNodeGroups[0].instanceType`` from ``t2.2xlarge`` to ``t3.2xlarge``, as instructed in the guide, since worker nodes of type ``t3.2xlarge`` are required for deploying both MLflow and Kubeflow.

Setup Juju
----------

Set up your local ``juju`` to talk to the remote Kubernetes cloud. First, install Juju with:

.. code-block:: bash

    sudo snap install juju --classic

Connect it to Kubernetes:

.. code-block:: bash

    juju add-k8s kubeflow

Create the controller:

.. code-block:: bash

    juju bootstrap --no-gui kubeflow kubeflow-controller

.. note:: we chose the name ``kubeflow-controller``, but you can choose any other name.

Add a Juju model:

.. code-block:: bash

    juju add-model kubeflow

Deploy MLflow bundle
---------------------

Deploy the MLflow bundle with the following command:

.. code-block:: bash

    juju deploy mlflow --channel=2.1/edge --trust

Wait until all charms are in the active state. You can check the state of the charms with the command:

.. code-block:: bash

    juju status --watch 5s --relations

Deploy Kubeflow bundle
----------------------

Deploy the Kubeflow bundle with the following command:

.. code-block:: bash

    juju deploy kubeflow --channel=1.7/stable --trust

Wait until all charms are in the active state. You can check the state of the charms with the command:

.. code-block:: bash

    juju status --watch 5s --relations

Relate MLflow to Kubeflow
-------------------------

The resource dispatcher is used to connect MLflow with Kubeflow. In particular, it is responsible for configuring MLflow related Kubernetes objects for Kubeflow user namespaces. Deploy the resource dispatcher to the cluster with the command:

.. code-block:: bash

    juju deploy resource-dispatcher --channel edge --trust

Relate the resource dispatcher to MLflow with the following commands:

.. code-block:: bash

    juju relate mlflow-server:secrets resource-dispatcher:secrets
    juju relate mlflow-server:pod-defaults resource-dispatcher:pod-defaults

Wait until all charms are in the active state. You can check the state of the charms with the command:

.. code-block:: bash

    juju status --watch 5s --relations

Configure Kubeflow dashboard
----------------------------

Get the hostname from the ``istio-ingressgateway-workload`` Kubernetes load balancer service:

.. code-block:: bash

    export INGRESS_HOST=$(kubectl get svc -n kubeflow istio-ingressgateway-workload -o jsonpath='{.status.loadBalancer.ingress[0].hostname}')

Then, configure OIDC and DEX with the ``INGRESS_HOST`` we just retrieved, and also a username and password of your choosing:

.. code-block:: bash
    
    juju config dex-auth public-url="http://${INGRESS_HOST}"
    juju config oidc-gatekeeper public-url="http://${INGRESS_HOST}"
    juju config dex-auth static-password=user123
    juju config dex-auth static-username=user123@email.com

Wait until all charms are in the active state. You can check the state of the charms with the command:

.. code-block:: bash

    juju status --watch 5s --relations

Now you can access the Kubeflow dashboard at the value from ``INGRESS_HOST`` in your browser.