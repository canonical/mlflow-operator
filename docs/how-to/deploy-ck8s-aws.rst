Deploy Charmed MLflow to Charmed Kubernetes on AWS
========================================================

+------------+---------+
| Component  | Version |
+============+=========+
| MLflow     | 2       |
+------------+---------+

This guide shows how to connect Juju to an existing `Charmed Kubernetes <https://ubuntu.com/kubernetes/charmed-k8s>`_ (CK8s) cluster and deploy the MLflow bundle on top of it.

Prerequisites
-------------

We assume that you have access to a CK8s cluster using ``kubectl``. If you don't have a cluster set up, you can follow this guide: `Create CK8s on AWS <../create-ck8s-aws>`_.

Install Juju
------------

Install Juju:

.. code-block:: bash

   sudo snap install juju --classic --channel=2.9/stable

Connect Juju to Charmed Kubernetes cluster
------------------------------------------

Configure Juju to communicate with the CK8s cluster by creating a controller:

.. code-block:: bash

   juju add-k8s charmed-k8s-aws --controller $(juju switch | cut -d: -f1) \
    --storage=cdk-ebs

Create a model. The model name is up to you. However, if you plan to connect MLflow with Kubeflow you must use ``kubeflow`` as the model name.

.. code-block:: bash

   juju add-model kubeflow charmed-k8s-aws

Deploy MLflow bundle
--------------------

Deploy the MLflow bundle:

.. code-block:: bash

   juju deploy mlflow --channel=2.1/edge --trust

Wait until the deployments are active:

.. code-block:: bash

   juju-wait -m kubeflow -t 2700

Connect to MLflow dashboard
---------------------------

By default, the MLflow UI is exposed as a NodePort Kubernetes service, accessible at each node's IP address. MLflow runs on port 31380 by default. AWS nodes are EC2 instances. To connect to an instance, it must be configured to allow traffic to this port.

You can connect to any EC2 instance in the cluster. List all available nodes in your Kubernetes cluster and choose any ``EXTERNAL-IP`` that you will use to access the MLflow UI:

.. code-block:: bash

   kubectl get nodes -o wide

In your AWS account find the EC2 instance with that particular ``EXTERNAL-IP`` and enable access to the port 31380 in the inbound rules of the security group. To see how, consult `AWS docs <https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/authorizing-access-to-an-instance.html>`_.

Open a web browser and visit ``<nodes-ip-address>:31380`` to access the MLflow UI.
