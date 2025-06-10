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

Your machine should also have an SSH tunnel open to the VM with port forwarding and a SOCKS proxy. 
See `How to setup SSH VM Access <https://discourse.charmhub.io/t/how-to-setup-ssh-vm-access-with-port-forwarding/10872>`_ for more details.

.. note:: 
   This tutorial assumes you are running all commands on the VM, through the open SSH tunnel. 
   Also that you are using the web browser on your local machine to access the Kubeflow and MLflow dashboards.

Deploy MLflow
-------------

Follow the steps in this tutorial to deploy MLflow on your VM: :doc:`mlflow`. 
Before moving on with this tutorial, confirm that you have access to the MLflow User Interface (UI) on ``http://localhost:31380``.

.. _kubeflow-section:

Deploy Kubeflow 
----------------

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

Then, relate the Resource dispatcher to MLflow as follows:

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


Integrate MLflow with Notebooks
-------------------------------

In this section, you are going to create a Kubeflow Notebook server and connect it to MLflow. 

1. Start by navigating to the MLflow dashboard at ``http://10.64.140.43.nip.io/``. 
Use the username and password you configured in the :ref:`kubeflow-section` section.

2. Click on ``Start setup`` to setup the Kubeflow user for the first time and Select ``Finish`` to finish the process.

3. Now go back to the dashboard. From the left panel, choose ``Notebooks``. 
Select ``+New Notebook``.

At this point, name the Notebook and choose the desired image and resource limits. 
For example, you can use the following details:

* ``Name``: ``test-notebook``.
* Expand the *Custom Notebook* section and select the ``jupyter-tensorflow-full`` image.

Now, enable your Notebook server to access MLflow.
Scroll down to ``Data Volumes -> Advanced options`` and from the ``Configurations`` dropdown, choose the following options:

* Allow access to Kubeflow pipelines.
* Allow access to MinIO.
* Allow access to MLflow.

Click on ``Launch`` to launch the Notebook server.

.. note:: 
   The notebook server may take a few minutes to initialise.

Once the Notebook server is ready, you'll see it listed in the Notebooks table with a success status. 
At this point, select ``Connect`` to connect to it.

To ensure that MLflow is accessible, create a new notebook and add a cell with the following command:

.. code-block:: bash

   !printenv | grep MLFLOW

Run the cell. 
This will print out ``MLFLOW_S3_ENDPOINT_URL`` and ``MLFLOW_TRACKING_URI`` variables, confirming MLflow is connected.

Run MLflow examples
-------------------

To run MLflow examples on your newly created Notebook server, click on the source control icon in the leftmost navigation bar.

From the menu, choose ``Clone a Repository``, and clone the following repository: ``https://github.com/canonical/charmed-kubeflow-uats.git``.

This clones the ``charmed-kubeflow-uats`` repository onto the Notebook server. 
Enter the directory and navigate to the ``tests/notebooks`` sub-folder.

You will see the following folders:

* ``mlflow-kserve``: Demonstrates how to interact with MLflow and KServe from inside a notebook. This example trains a simple ML model, stores it in MLflow, deploys it with KServe from MLflow, and runs an inference service.

* ``mlflow-minio``: Demonstrates how to interact with MinIO from inside a notebook. This example shows how to use mounted MinIO secrets to access the MinIO object store.

* ``mlflow``: Demonstrates how to interact with MLflow from inside a notebook. This example uses a simple regression model that is stored in the MLflow registry.


