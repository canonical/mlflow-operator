Getting Started with Charmed MLflow and Kubeflow
================================================

+-----------+---------+
| Component | Version |
+-----------+---------+
|   MLflow  |   2.15  |
+-----------+---------+
|  Kubeflow |   1.9   |
+-----------+---------+

This tutorial gets you started with Charmed MLflow integrated with `Charmed Kubeflow (CKF) <https://charmed-kubeflow.io/docs>`_.

Prerequisites
-------------

This guide assumes you are deploying Kubeflow and MLflow on a public cloud Virtual Machine (VM) with the following specifications:

- Runs Ubuntu 20.04 (focal) or later.
- Has at least 4 cores, 32GB RAM and 200GB of disk space available.

Also, your machine should meet the following requirements:

- Has an SSH tunnel open to the VM with port forwarding and a SOCKS proxy. To see how to set this up, see `How to setup SSH VM Access <https://charmed-kubeflow.io/docs/how-tosetup-ssh-vm-access-with-port-forwarding>`_.
- Runs Ubuntu 20.04 (focal) or later.
- Has a web browser installed e.g. Chrome / Firefox / Edge.

In the remainder of this tutorial, unless otherwise stated, it is assumed you will be running all command line operations on the VM, through the open SSH tunnel. It's also assumed you'll be using the web browser on your local machine to access the Kubeflow and MLflow dashboards.

Deploy MLflow
-------------

Follow the steps in this tutorial to deploy MLflow on your VM: :doc:`mlflow`. Before moving on with this tutorial, confirm that you can now access the MLflow UI on ``http://localhost:31380``.

.. _kubeflow-section:

Deploy Kubeflow bundle
----------------------

Let's deploy Charmed Kubeflow alongside MLflow. Run the following command to initiate the deployment:

.. code-block:: bash

   juju deploy kubeflow --trust  --channel=1.9/stable

Set credentials for your Kubeflow deployment:

.. code-block:: bash

   juju config dex-auth static-username=admin
   juju config dex-auth static-password=admin

Deploy Resource Dispatcher
--------------------------

Next, deploy the resource dispatcher. The resource dispatcher is an optional component which distributes Kubernetes objects related to MLflow credentials to all user namespaces in Kubeflow. This means that all your Kubeflow users can access the MLflow model registry from their namespaces. To deploy the dispatcher, run the following command:

.. code-block:: bash

   juju deploy resource-dispatcher --channel 2.0/stable --trust

This deploys the latest stable version of the dispatcher. See `Resource Dispatcher on GitHub <https://github.com/canonical/resource-dispatcher>`_ for more details. Now, relate the dispatcher to MLflow as follows:

.. code-block:: bash

   juju integrate mlflow-server:secrets resource-dispatcher:secrets
   juju integrate mlflow-server:pod-defaults resource-dispatcher:pod-defaults

To deploy sorted MLflow models using KServe, create the required relations as follows:

.. code-block:: bash

   juju integrate mlflow-minio:object-storage kserve-controller:object-storage
   juju integrate kserve-controller:service-accounts resource-dispatcher:service-accounts
   juju integrate kserve-controller:secrets resource-dispatcher:secrets

Monitor The Deployment
----------------------

Now, at this point, we've deployed MLflow and Kubeflow and we've related them via the resource dispatcher. But that doesn't mean our system is ready yet: Juju will need to download charm data from CharmHub and the charms themselves will take some time to initialise.

So, how do you know when all the charms are ready, then? You can do this using the ``juju status`` command. First, let's run a basic status command and review the output. Run the following command to print out the status of all the components of Juju:

.. code-block:: bash

   juju status

Review the output for yourself. You should see some summary information, a list of Apps and associated information, and another list of Units and their associated information. Don't worry too much about what this all means for now. If you're interested in learning more about this command and its output, see the `Juju Status command <https://juju.is/docs/juju/juju-status>`_.

The main thing we're interested in at this stage is the statuses of all the applications and units running through Juju. We want all the statuses to eventually become ``active``, indicating that the bundle is ready. Run the following command to keep a watch on the components which are not active yet:

.. code-block:: bash

   juju status --watch 5s

This will periodically run a ``juju status`` command.

Don't be surprised if some of the components' statuses change to ``blocked`` or ``error`` every now and then. This is expected behaviour, and these statuses should resolve by themselves as the bundle configures itself. However, if components remain stuck in the same error states, consult the troubleshooting steps below.

.. note::

   It can take up to 15 minutes for all charms to be downloaded and initialised.

Integrate MLflow with Kubeflow Dashboard
----------------------------------------

You can integrate your charmed MLflow deployment with the Kubeflow dashboard by running following commands:

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


Integrate MLflow with Notebook
------------------------------

In this section, you are going to create a Kubeflow notebook server and connect it to MLflow. 

First, to be able to use MLflow credentials in your Kubeflow notebook, go to the MLflow dashboard at ``http://10.64.140.43.nip.io/`` 
and use the username and password you configured in the previous :ref:`kubeflow-section` section.
For example, ``admin`` and ``admin``.

Click on ``Start setup`` to setup the Kubeflow user for the first time.

Select ``Finish`` to finish the process.

Now a Kubernetes namespace is created for your user. 

Now go back to the dashboard. From the left panel, choose ``Ǹotebooks``. 
Select ``+New Notebook``.

At this point, name the notebook as you prefer, and choose the desired image and resource limits. 
For example, you can use the following details:

1. ``Name``: ``test-notebook``.
2. Expand the *Custom Notebook* section and for ``image``, select ``kubeflownotebookswg/jupyter-tensorflow-full:v1.9.0``.

Now, to allow your notebook server access to MLflow, you need to enable some configuration options. 
Scroll down to ``Data Volumes -> Advanced options`` and from the ``Configurations`` dropdown, choose the following options:

1. Allow access to Kubeflow pipelines.
2. Allow access to MinIO.
3. Allow access to MLflow.

.. note:: Remember we related Kubeflow to MLflow earlier using the resource dispatcher? This is why we're seeing the MinIO and MLflow options in the dropdown!

Great, that's all the configuration for the notebook server done. Hit the Launch button to launch the notebook server. Be patient, the notebook server will take a little while to initialise.

When the notebook server is ready, you'll see it listed in the Notebooks table with a success status. At this point, select ``Connect`` to connect to the notebook server.

When you connect to the notebook server, you'll be taken to the notebook environment in a new tab. Because of our earlier configurations, this environment is now connected to MLflow in the background. This means the notebooks we create here can access MLflow. Cool!

To test this, create a new notebook and paste the following command into it, in a cell:

.. code-block:: bash

   !printenv | grep MLFLOW

Run the cell. This will print out two environment variables ``MLFLOW_S3_ENDPOINT_URL`` and ``MLFLOW_TRACKING_URI``, confirming MLflow is indeed connected.

Great, we've launched a notebook server that's connected to MLflow! Now let's upload some example notebooks to this server to see MLflow in practice.

Run MLflow examples
-------------------

To run MLflow examples on your newly created notebook server, click on the source control icon in the leftmost navigation bar.

From the menu, choose the ``Clone a Repository`` option.

Now insert this repository address ``https://github.com/canonical/charmed-kubeflow-uats.git``.

This clones a whole ``charmed-kubeflow-uats`` repository onto the notebook server. The cloned repository is a folder on the server, with the same name as the remote repository. Go inside the folder and after that, choose the ``tests/notebooks`` sub-folder.

There you find following folders:

- ``mlflow-kserve``: demonstrates how to talk to MLflow and KServe from inside a notebook. This example trains a simple ML model, stores it in MLflow, deploys it with KServe from MLflow and runs inference.
- ``mlflow-minio``: demonstrates how to talk to MinIO from inside a notebook. This example shows how you can use mounted MinIO secrets to talk to MinIO object store.
- ``mlflow``: demonstrates how to talk to MLflow from inside a notebook. The example uses a simple regression model which is stored in the MLflow registry.

Go ahead, try those notebooks out for yourself! You can run them cell by cell using the run button, or all at once using the double chevron `>>`.
