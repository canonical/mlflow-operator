Create an MLOps-ready Charmed Kubernetes cluster
================================================

This how-to guide will show you how to create a Charmed Kubernetes (CK8s) cluster with an appropriate configuration for deploying an MLOps platforms such as Kubeflow or MLflow.

**Prerequisites**

- A local machine with Ubuntu 22.04 or later.
- An AWS account (`How to create an AWS account <https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-creating.html>`_).

Install and set up AWS CLI
---------------------------

First, `install the AWS CLI <https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html>`_ on your local machine, and then set it up. You can use any of the authentication methods available for the AWS CLI. For example, you can use `IAM user credentials <https://docs.aws.amazon.com/cli/latest/userguide/cli-authentication-user.html>`_.

Install other tools
-------------------

To install some helpful tools, run this command:

.. code-block:: bash

   sudo snap install juju --classic --channel=2.9/stable
   for snap in juju-wait kubectl jq; \
     do sudo snap install $snap --classic; \
   done

This installs the following:

* ``juju``: Needed to deploy and manage the CK8s cluster.
* ``juju-wait``: CLI tool used for waiting during juju deployments.
* ``kubectl``: Kubernetes client used to communicate with a Kubernetes cluster.
* ``jq``: A lightweight and versatile command-line tool for parsing and manipulating JSON data.



Setup Juju with AWS
-------------------

Set up Juju to communicate with AWS.

.. code-block:: bash

   juju add-credential aws

You will be prompted for information related to your AWS account that you provided while setting up the AWS CLI (e.g., access key, secret access key).

Create Juju controller
----------------------

Bootstrap a Juju controller that will be responsible for deploying cluster applications.

.. code-block:: bash

   juju bootstrap aws kf-controller

Deploy Charmed Kubernetes 1.24
------------------------------

Clone the `Charmed Kubernetes bundle repository <https://github.com/charmed-kubernetes/bundle.git>`_, and update CPU, disk, and memory constraints to meet Kubeflow requirements.

.. code-block:: bash

   git clone https://github.com/charmed-kubernetes/bundle.git
   sed -i '/^ *charm: kubernetes-worker/,/^ *[^:]*:/s/constraints: cores=2 mem=8G root-disk=16G/constraints: cores=8 mem=32G root-disk=200G/' ./bundle/releases/1.24/bundle.yaml

Deploy the Charmed Kubernetes bundle on AWS with the storage overlay. This overlay enables you to create Kubernetes volumes backed by AWS EBS.

.. code-block:: bash

   juju deploy ./bundle/releases/1.24/bundle.yaml \
     --overlay ./bundle/overlays/aws-storage-overlay.yaml --trust

Wait until all components are ready.

.. code-block:: bash

   juju-wait -m default -t 3600

Retrieve the Kubernetes configuration from the control plane leader unit.

.. code-block:: bash

   mkdir ~/.kube
   juju ssh kubernetes-control-plane/leader -- cat config > ~/.kube/config

Now you can use ``kubectl`` to talk to your newly created Charmed Kubernetes cluster.