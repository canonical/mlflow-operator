.. _tutorial_get_started_ckf:

Get started with Charmed MLflow and Kubeflow
============================================

+-----------+---------+
| Component | Version |
+-----------+---------+
|   MLflow  |   2.22  |
+-----------+---------+
|  Kubeflow |   1.10  |
+-----------+---------+

This tutorial gets you started with Charmed MLflow integrated with `Charmed Kubeflow (CKF) <https://charmed-kubeflow.io/docs>`_.

Requirements
-------------

This guide assumes you are deploying Kubeflow and MLflow on a public cloud Virtual Machine (VM) with the following specifications:

- Runs Ubuntu 22.04 or later.
- Has at least 4 cores, 32GB RAM and 200GB of disk space available.

Your machine should also have an SSH tunnel open to the VM with port forwarding and a SOCKS proxy. To see how to set this up, see `How to setup SSH VM Access <https://discourse.charmhub.io/t/how-to-setup-ssh-vm-access-with-port-forwarding/10872>`_.

In the remainder of this tutorial, unless otherwise stated, it is assumed you will be running all command line operations on the VM, through the open SSH tunnel. It's also assumed you'll be using the web browser on your local machine to access the Kubeflow and MLflow dashboards.

Deploy MLflow
-------------

Follow the steps in this tutorial to deploy MLflow on your VM: :doc:`mlflow`. Before moving on with this tutorial, confirm that you can now access the MLflow UI on ``http://localhost:31380``.

.. _kubeflow-section:

Deploy the Kubeflow bundle
--------------------------

To deploy Kubeflow along MLflow, run:

.. code-block:: bash

   juju deploy kubeflow --trust  --channel=1.10/stable

Once the deployment is completed, you will see this message:

.. code-block:: bash
				
	Deploy of bundle completed.

.. note:: After the deployment, the bundle components need some time to initialise and establish communication with each other. This process may take up to 20 minutes.

Check the status of the components with:

.. code-block:: bash
				
	juju status

Use the ``watch`` option to continuously track their status:

.. code-block:: bash
				
	juju status --watch 5s

CKF is ready when all the applications and units are in active status. During the configuration process, some of the components may momentarily change to a blocked or error state. This is an expected behaviour that should resolve as the bundle configures itself.
	
Set credentials for your Kubeflow deployment:

.. code-block:: bash

   juju config dex-auth static-username=admin
   juju config dex-auth static-password=admin
  
Deploy Resource Dispatcher
--------------------------

Resource dispatcher is an optional component which distributes Kubernetes objects related to MLflow credentials to all user namespaces in Kubeflow. This enables all Kubeflow users to access the MLflow model registry from their namespaces. To deploy resource dispatcher, run the following command:

.. code-block:: bash

   juju deploy resource-dispatcher --channel 2.0/stable --trust

> See `Resource Dispatcher on GitHub <https://github.com/canonical/resource-dispatcher>`_ for more details.

Then, relate the resource dispatcher to MLflow as follows:

.. code-block:: bash

   juju integrate mlflow-server:secrets resource-dispatcher:secrets
   juju integrate mlflow-server:pod-defaults resource-dispatcher:pod-defaults

To deploy sorted MLflow models using KServe, create the required relations as follows:

.. code-block:: bash

   juju integrate mlflow-minio:object-storage kserve-controller:object-storage
   juju integrate kserve-controller:service-accounts resource-dispatcher:service-accounts
   juju integrate kserve-controller:secrets resource-dispatcher:secrets

Integrate MLflow with Kubeflow Dashboard
----------------------------------------

You can integrate the MLflow server with the Kubeflow dashboard by running:

.. code-block:: bash

   juju integrate mlflow-server:ingress istio-pilot:ingress
   juju integrate mlflow-server:dashboard-links kubeflow-dashboard:links

Now you should see the MLflow tab in the left sidebar of your Kubeflow dashboard at:

.. code-block:: bash
   
   http://10.64.140.43.nip.io/

.. note:: 
   
   The address of your Kubeflow dashboard may differ depending on your setup. You can always check its URL by running: 
   
   .. code-block:: bash
      
      microk8s kubectl -n kubeflow get svc istio-ingressgateway-workload -o jsonpath='{.status.loadBalancer.ingress[0].ip}'


Integrate MLflow with Notebooks
-------------------------------

In this section, you are going to create a Kubeflow notebook server and connect it to MLflow. 

First, visit the MLflow dashboard at ``http://10.64.140.43.nip.io/`` and use the username and password you configured in the :ref:`kubeflow-section` section.

Click on ``Start setup`` to setup the Kubeflow user for the first time.

Select ``Finish`` to finish the process.

Now go back to the dashboard. From the left panel, choose ``Notebooks``. 
Select ``+New Notebook``.

At this point, name the notebook as you prefer, and choose the desired image and resource limits. 
For example, you can use the following details:

1. ``Name``: ``test-notebook``.
2. Expand the *Custom Notebook* section and for ``image``, select ``kubeflownotebookswg/jupyter-tensorflow-full:v1.10.0``.

Now, to allow your notebook server access to MLflow, you need to enable some configuration options. Scroll down to ``Data Volumes -> Advanced options`` and from the ``Configurations`` dropdown, choose the following options:

1. Allow access to Kubeflow pipelines.
2. Allow access to MinIO.
3. Allow access to MLflow.

Clock on the ``Launch`` button to launch the notebook server.

.. note:: The notebook server may take a few minutes to initialise.

When the notebook server is ready, you'll see it listed in the Notebooks table with a success status. At this point, select ``Connect`` to connect to the notebook server.

To ensure that MLflow is accessible, create a new notebook and paste the following command into it, in a cell:

.. code-block:: bash

   !printenv | grep MLFLOW

Run the cell. This will print out two environment variables ``MLFLOW_S3_ENDPOINT_URL`` and ``MLFLOW_TRACKING_URI``, confirming MLflow is indeed connected.

Run MLflow examples
-------------------

To run MLflow examples on your newly created notebook server, click on the source control icon in the leftmost navigation bar.

From the menu, choose the ``Clone a Repository`` option, and close the following repository: ``https://github.com/canonical/charmed-kubeflow-uats.git``.

This clones the ``charmed-kubeflow-uats`` repository onto the notebook server. Enter the directory and choose the ``tests/notebooks`` sub-folder.

You will see the following folders:

- ``mlflow-kserve``: demonstrates how to talk to MLflow and KServe from inside a notebook. This example trains a simple ML model, stores it in MLflow, deploys it with KServe from MLflow and runs an inference service.
- ``mlflow-minio``: demonstrates how to talk to MinIO from inside a notebook. This example shows how you can use mounted MinIO secrets to talk to MinIO object store.
- ``mlflow``: demonstrates how to talk to MLflow from inside a notebook. The example uses a simple regression model which is stored in the MLflow registry.

