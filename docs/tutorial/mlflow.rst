.. _tutorial_get_started:

Get started with Charmed MLflow
==================================

+-----------+---------+
| Component | Version |
+-----------+---------+
|   MLflow  |    2    |
+-----------+---------+

Welcome to the tutorial on Charmed MLflow! `MLflow <https://mlflow.org/>`_ is an open-source platform, used for managing machine learning workflows. It has four primary functions that include experiment tracking, model registry, model management and code reproducibility.

So wait, what does "Charmed MLflow" mean? Is it the same thing as MLflow? Yes and no. MLflow is a complex application, consisting of many components running together and communicating with each other. Charmed MLflow is a `charm bundle <https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/bundle/>`_ that allows us to deploy MLflow quickly and easily. Don't worry too much about what a "charm bundle" is right now. The key thing is that it's going to make deploying MLflow very convenient for us: we'll get MLflow up and running with just a few command line commands!

In this tutorial, we're going to explore Charmed MLflow in a practical way. Using the `Juju <https://juju.is/>`_ CLI tool, we'll deploy MLflow to a local `MicroK8s <https://microk8s.io/>`_ cloud.

Requirements
-------------

This tutorial assumes you are running it on a local machine with the following specs:

* Runs Ubuntu 22.04 or later
* Has at least 50GB free disk space

Install and prepare MicroK8s
----------------------------
Let's install MicroK8s. MicroK8s is installed from a snap package. The published snap maintains different ``channels`` for different releases of Kubernetes.

.. code-block:: bash

   sudo snap install microk8s --channel=1.29-strict/stable

For MicroK8s to work without having to use ``sudo`` for every command, it creates a group called ``microk8s``. To make it more convenient to run commands, you will add the current user to this group:

.. code-block:: bash

   sudo usermod -a -G snap_microk8s ubuntu
   newgrp snap_microk8s

Enable the following MicroK8s addons to configure your Kubernetes cluster with extra services needed to run Charmed Kubeflow.

.. code-block:: bash

   sudo microk8s enable dns hostpath-storage metallb:10.64.140.43-10.64.140.49 rbac

Here, we added a ``dns`` service, so the applications can find each other, storage, an ingress controller so we can access Kubeflow components and the ``MetalLB`` load-balancer application.
You can see that we added some detail when enabling ``MetalLB``, in this case the address pool to use.

> See More : `MicroK8s | How to use addons <https://microk8s.io/docs/addons>`_

We've now installed and configured MicroK8s. It will start running automatically, but can take 5 minutes or so before it's ready for action. Run the following command to tell MicroK8s to report its status to us when it's ready:

.. code-block:: bash

   microk8s status --wait-ready

Be patient - this command may not return straight away. The ``--wait-ready`` flag tells MicroK8s to wait for the Kubernetes services to initialise before returning. Once MicroK8s is ready, you will see something like the following output:

.. code-block:: bash

   microk8s is running

Below this there will be a bunch of other information about the cluster.

Great, we have now installed and configured MicroK8s, and it's running and ready!

Install Juju
------------
`Juju <https://juju.is/>`_ is an operation Lifecycle manager (OLM) for clouds, bare metal or Kubernetes. We will be using it to deploy and manage the components which make up Kubeflow.

To install Juju from snap, run this command:

.. code-block:: bash

   sudo snap install juju --channel=3.4/stable

On some machines there might be a missing folder which is required for Juju to run correctly. To ensure that this folder exists, run:

.. code-block:: bash
   
   mkdir -p ~/.local/share

As a next step, configure MicroK8s to work properly with Juju by running:

.. code-block:: bash

   microk8s config | juju add-k8s my-k8s --client

Now, run the following command to deploy a Juju controller to the Kubernetes we set up with MicroK8s:

.. code-block:: bash

   juju bootstrap microk8s

Sit tight while the command completes! The controller may take a minute or two to deploy.

The controller is the agent of Juju, running on Kubernetes, which can be used to deploy and control the components of Kubeflow.

Next, we'll need to add a model for Kubeflow to the controller. Run the following command to add a model called ``kubeflow``:

.. code-block:: bash

   juju add-model kubeflow

.. note:: The model name here can be anything. We're just using ``kubeflow`` because often you may want to deploy MLflow along with Kubeflow, and in that case, the model name must be ``kubeflow``. So it's not a bad habit to have.

The controller can work with different ``models``, which map 1:1 to namespaces in Kubernetes. In this case, the model name must be ``kubeflow``, due to an assumption made in the upstream Kubeflow Dashboard code.

Great job: Juju has now been installed and configured for Kubeflow!

Deploy MLflow bundle
--------------------
Before deploying, run these commands:

.. code-block:: bash

   sudo sysctl fs.inotify.max_user_instances=1280
   sudo sysctl fs.inotify.max_user_watches=655360

We need to run the above because under the hood, MicroK8s uses ``inotify`` to interact with the filesystem, and in large MicroK8s deployments sometimes the default ``inotify`` limits are exceeded.

Let's now use Juju to deploy Charmed MLflow. Run the following command:

.. code-block:: bash

   juju deploy mlflow --channel=2.15/stable --trust

This deploys the stable version of MLflow with `MinIO <https://min.io/>`_ as the object storage and `MySQL` as the metadata store.

Once the deployment is completed, you get a message such as:

.. code-block:: bash
   
   Deploy of bundle completed.

You can use the following command to check the status of all the model components:

.. code-block:: bash

   juju status

The deployment is ready when the statuses of all the applications and the units in the bundle have an active status. You can also use this option to continuously watch the status of the model:

.. code-block:: bash

   juju status --watch 5s

During the deployment process, some of the components statuses may momentarily change to blocked or error state. This is an expected behaviour, and these statuses should resolve by themselves as the bundle configures.

Access MLflow
-------------
To access MLflow, visit the following URL in your web browser:

.. code-block:: bash

   http://localhost:31380/

This will take you to the MLflow UI.

.. note:: by default Charmed MLflow creates a `NodePort <https://kubernetes.io/docs/concepts/services-networking/service/#type-nodeport>`_ on port 31380 where you can access the MLflow UI.

That's it! Charmed MLflow has been deployed locally with MicroK8s and Juju. You can now start using MLflow.

Reference: Object storage credentials
-------------------------------------
To use MLflow you need to have credentials to the object storage. The aforementioned bundle comes with MinIO. To get the ``MinIO`` credentials run the following command:

.. code-block:: bash

   juju run mlflow-server/0 get-minio-credentials

This action will output ``secret-key`` and ``secret-access-key``.
