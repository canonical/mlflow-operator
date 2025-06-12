Deploy Charmed MLflow and Kubeflow to EKS
=========================================

This guide shows how to deploy Charmed MLflow alongside Kubeflow on `AWS Elastic Kubernetes Service <https://aws.amazon.com/eks/>`_ (EKS). 
In this guide, you will create an AWS EKS cluster, connect `Juju <https://juju.is/>`_ to it, deploy the MLflow and Kubeflow bundles, and relate them to each other.

Requirements
-------------

- An AWS account (`How to create an AWS account <https://docs.aws.amazon.com/accounts/latest/reference/manage-acct-creating.html>`_).
- Ubuntu 22.04 or later.

Create an EKS cluster
----------------------

See the `EKS creation guide <https://discourse.charmhub.io/t/create-an-eks-cluster-for-use-with-an-mlops-platform/10983>`_ to learn how to create an EKS cluster where Charmed MLflow will be deployed.

.. note:: 
   Make sure to change the value of ``instanceType`` under ``managedNodeGroups[0].instanceType`` from ``t2.2xlarge`` to ``t3.2xlarge``, 
   as worker nodes of type ``t3.2xlarge`` or larger are required to deploy both MLflow and Kubeflow.

Set up Juju
------------

First, install Juju with:

.. code-block:: bash

    sudo snap install juju --channel=3.6/stable

Connect it to Kubernetes (K8s):

.. code-block:: bash

    juju add-k8s kubeflow

Create the controller:

.. code-block:: bash

    juju bootstrap --no-gui kubeflow kubeflow-controller

.. note:: You can use any name for the controller.

Add the ``kubeflow`` model to your Juju controller:

.. code-block:: bash

    juju add-model kubeflow

.. note:: You must choose ``kubeflow`` as the model name to connect MLflow to Kubeflow.

Deploy MLflow
--------------

Deploy the `MLflow bundle <https://charmhub.io/mlflow>`_ as follows:

.. code-block:: bash

    juju deploy mlflow --channel=2.22/stable --trust

Deploy Kubeflow
---------------

To deploy Kubeflow along with MLflow, run the following command:

.. code-block:: bash

   juju deploy kubeflow --trust  --channel=1.10/stable

Once the deployment is completed, you will see the following message:

.. code-block:: bash
                
   Deploy of bundle completed.

.. note:: 
   The bundle components need some time to initialise and establish communication with each other. 
   This process may take up to 20 minutes.

Check the status of the components with:

.. code-block:: bash
                
    juju status

The deployment is ready when all the applications and units in the bundle are in ``active`` status. 
You can also use the ``watch`` option to continuously monitor the statuses:

.. code-block:: bash
                
    juju status --watch 5s

During the deployment process, some of the components statuses may momentarily change to blocked or error state. 
This is an expected behaviour, and these statuses should resolve by themselves as the bundle configures.

Set credentials for your Kubeflow deployment:

.. code-block:: bash

   juju config dex-auth static-username=admin
   juju config dex-auth static-password=admin

Deploy Resource dispatcher
--------------------------

The Resource dispatcher operator is an optional component which distributes K8s objects related to MLflow credentials to all user namespaces in Kubeflow. 
This enables all Kubeflow users to access the MLflow model registry from their namespaces. 
Deploy it as follows:

.. code-block:: bash

   juju deploy resource-dispatcher --channel 2.0/stable --trust

See `Resource Dispatcher <https://github.com/canonical/resource-dispatcher>`_ for more details.

Then, relate the Resource dispatcher to Charmed MLflow as follows:

.. code-block:: bash

   juju integrate mlflow-server:secrets resource-dispatcher:secrets
   juju integrate mlflow-server:pod-defaults resource-dispatcher:pod-defaults

To deploy MLflow models using KServe, create the required relations as follows:

.. code-block:: bash

   juju integrate mlflow-minio:object-storage kserve-controller:object-storage
   juju integrate kserve-controller:service-accounts resource-dispatcher:service-accounts
   juju integrate kserve-controller:secrets resource-dispatcher:secrets


Integrate MLflow with Kubeflow dashboard
----------------------------------------

You can integrate the MLflow server with the Kubeflow dashboard as follows:

.. code-block:: bash

   juju integrate mlflow-server:ingress istio-pilot:ingress
   juju integrate mlflow-server:dashboard-links kubeflow-dashboard:links

Now you should see the MLflow tab in the left-hand sidebar of your Kubeflow dashboard at:

.. code-block:: bash
   
   http://10.64.140.43.nip.io/

.. note:: 
   
   The address of your Kubeflow dashboard may differ depending on your setup. Check its URL by running: 
   
   .. code-block:: bash
      
      microk8s kubectl -n kubeflow get svc istio-ingressgateway-workload -o jsonpath='{.status.loadBalancer.ingress[0].ip}'
