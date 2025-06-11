Deploy Charmed MLflow and Kubeflow to EKS
=========================================

This guide shows how to deploy Charmed MLflow alongside Kubeflow on `AWS Elastic Kubernetes Service <https://aws.amazon.com/eks/>`_ (EKS). In this guide, you will create an AWS EKS cluster, connect Juju to it, deploy the MLflow and Kubeflow bundles, and relate them to each other.

Requirements
-------------

- An AWS account (`How to create an AWS account <https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-creating.html>`_).
- A machine that runs Ubuntu 22.04 or a newer version.

Deploy EKS cluster
-------------------

See our `EKS creation guide <https://discourse.charmhub.io/t/create-an-eks-cluster-for-use-with-an-mlops-platform/10983>`_ for a complete guide on how to do this. Make sure to edit the ``instanceType`` field under ``managedNodeGroups[0].instanceType`` from ``t2.2xlarge`` to ``t3.2xlarge``, as instructed in the guide, since worker nodes of type ``t3.2xlarge`` or larger are required for deploying both MLflow and Kubeflow.

Setup Juju
----------

Set up your local ``juju`` to talk to the remote Kubernetes cloud. First, install Juju with:

.. code-block:: bash

    sudo snap install juju --channel=3.6/stable

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
--------------------

Deploy the MLflow bundle with the following command:

.. code-block:: bash

    juju deploy mlflow --channel=2.22/stable --trust

Deploy Kubeflow
---------------

To deploy Kubeflow along MLflow, run the following:

.. code-block:: bash

   juju deploy kubeflow --trust  --channel=1.10/stable

Once the deployment is completed, you will see this message:

.. code-block:: bash
                
   Deploy of bundle completed.

.. note:: 
   The bundle components need some time to initialise and establish communication with each other. 
   This process may take up to 20 minutes.

Check the status of the components with:

.. code-block:: bash
                
    juju status

Use the ``watch`` option to continuously track their status:

.. code-block:: bash
                
    juju status --watch 5s

CKF is ready when all the applications and units are in active status. 
During the configuration process, some of the components may momentarily change to a blocked or error state. This is an expected behaviour that should resolve as the bundle configures itself.

Set credentials for your Kubeflow deployment:

.. code-block:: bash

   juju config dex-auth static-username=admin
   juju config dex-auth static-password=admin

Deploy Resource dispatcher
--------------------------

The Resource dispatcher operator is an optional component which distributes Kubernetes objects related to MLflow credentials to all user namespaces in Kubeflow. 
This enables all Kubeflow users to access the MLflow model registry from their namespaces. 
Deploy it as follows:

.. code-block:: bash

   juju deploy resource-dispatcher --channel 2.0/stable --trust

See `Resource Dispatcher <https://github.com/canonical/resource-dispatcher>`_ for more details.

Then, relate the Resource dispatcher to MLflow:

.. code-block:: bash

   juju integrate mlflow-server:secrets resource-dispatcher:secrets
   juju integrate mlflow-server:pod-defaults resource-dispatcher:pod-defaults

To deploy sorted MLflow models using KServe, create the required relations as follows:

.. code-block:: bash

   juju integrate mlflow-minio:object-storage kserve-controller:object-storage
   juju integrate kserve-controller:service-accounts resource-dispatcher:service-accounts
   juju integrate kserve-controller:secrets resource-dispatcher:secrets


Integrate MLflow with Kubeflow dashboard
----------------------------------------

You can integrate the MLflow server with the Kubeflow dashboard by running:

.. code-block:: bash

   juju integrate mlflow-server:ingress istio-pilot:ingress
   juju integrate mlflow-server:dashboard-links kubeflow-dashboard:links

Now you should see the MLflow tab in the left-hand sidebar of your Kubeflow dashboard at:

.. code-block:: bash
   
   http://10.64.140.43.nip.io/

.. note:: 
   
   The address of your Kubeflow dashboard may differ depending on your setup. You can always check its URL by running: 
   
   .. code-block:: bash
      
      microk8s kubectl -n kubeflow get svc istio-ingressgateway-workload -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
