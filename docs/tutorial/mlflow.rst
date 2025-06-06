.. _tutorial_get_started:

Get started with Charmed MLflow
==================================

This guide describes how you can get started with Charmed MLflow, from deploying to accessing it. It is intended for system administrators and end users.

Charmed MLflow is a `charm bundle <https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/bundle/>`_ that facilitates a quick deployment of 
`MLflow <https://mlflow.org/>`_, an open-source platform, used for managing machine learning workflows,
including experiment tracking, model registry, model management and code reproducibility..

Requirements
-------------

* Ubuntu 22.04 or later.
* A host machine with at least 50GB of disk space available.

Install and configure dependencies 
----------------------------------

Charmed MLflow relies on:

- Kubernetes (K8s). This tutorial uses MicroK8s, an open-source zero-ops lightweight distribution of Kubernetes, to run a K8s cluster.
- A software orchestration engine. This tutorial uses `Juju <https://juju.is/>`_ to deploy and manage the Charmed MLflow components.

MicroK8s
~~~~~~~~~
You can install MicroK8s from a `snap package <https://snapcraft.io/>`. 
The published snap maintains different ``channels`` for different releases of Kubernetes.

.. code-block:: bash

   sudo snap install microk8s --channel=1.32/stable --classic

For MicroK8s to work without having to use ``sudo`` for every command, it creates a group called ``microk8s``. To make it more convenient to run commands, add the current user to this group:

.. code-block:: bash

   sudo usermod -a -G microk8s $USER
   newgrp microk8s

For deploying Charmed MLflow, you need additional features from the MicroK8s' default ones. 
These can be installed as MicroK8s add-ons. 
Run the following command to enable them:

.. code-block:: bash
   
   sudo microk8s enable dns hostpath-storage metallb:10.64.140.43-10.64.140.49 rbac

See `How to use MicroK8s add-ons <https://microk8s.io/docs/addons>`_ for more details.

To confirm that all add-ons are successfully enabled, check MicroK8s' status as follows:

.. code-block:: bash
   
   microk8s status

.. note:: The add-ons configuration may take a few minutes to complete before they are listed as enabled.

Juju
~~~~~

Juju is an operation Lifecycle manager (OLM) for clouds, bare metal or K8s. 
You will use it to deploy and manage the components which make up Charmed MLflow.

To install Juju from snap, run this command:

.. code-block:: bash

   sudo snap install juju --channel=3.6/stable

On some machines there might be a missing folder which is required for Juju to run correctly. To ensure that this folder exists, run:

.. code-block:: bash
   
   mkdir -p ~/.local/share

As a next step, configure MicroK8s to work properly with Juju by running:

.. code-block:: bash

   microk8s config | juju add-k8s my-k8s --client

Now, run the following command to deploy a Juju controller to MicroK8s:

.. code-block:: bash

   juju bootstrap microk8s

.. note:: The controller may take a few minutes to deploy.

The controller is the Juju agent, running on K8s, which can be used to deploy and control MLflow's components.

Next, you need to add a model for Kubeflow to the controller. 
Run the following command to add a model named ``kubeflow``:

.. code-block:: bash

   juju add-model kubeflow

.. note:: The model name here can be anything. In this tutorial, ``kubeflow`` is being used because you may want to deploy MLflow along with Kubeflow, and in that case, the model name must be ``kubeflow``.

Deploy MLflow bundle
--------------------
MicroK8s uses ``inotify`` to interact with the file system. 
This may lead to situations where large MicroK8s deployments exceed the default ``inotify`` limits. 
To increase the limits, run the following commands:

.. code-block:: bash

   sudo sysctl fs.inotify.max_user_instances=1280
   sudo sysctl fs.inotify.max_user_watches=655360

If you want these commands to persist across machine restarts, add these lines to ``/etc/sysctl.conf``:

.. code-block:: bash
                
    fs.inotify.max_user_instances=1280
    fs.inotify.max_user_watches=655360
   

Deploy now the MLflow bundle as follows:

.. code-block:: bash

   juju deploy mlflow --channel=2.22/stable --trust

This deploys the stable version of MLflow with `MinIO <https://min.io/>`_ as the object storage and `MySQL` as the metadata store.

Once the deployment is completed, you will see a message such as the following:

.. code-block:: bash
   
   Deploy of bundle completed.

You can use the following command to check the status of all model components:

.. code-block:: bash

   juju status

The deployment is ready when all the applications and units in the bundle are in active status. 
You can also use the ``watch`` option to continuously monitor the statuses:

.. code-block:: bash

   juju status --watch 5s

During the deployment process, some of the components statuses may momentarily change to blocked or error state. 
This is an expected behaviour, and these statuses should resolve by themselves as the bundle configures.

Access your deployment
-----------------------

To access your Charmed MLflow deployment, navigate to the following URL:

.. code-block:: bash

   http://localhost:31380/

This will take you to the MLflow User Interface (UI).

.. note:: by default Charmed MLflow creates a `NodePort <https://kubernetes.io/docs/concepts/services-networking/service/#type-nodeport>`_ on port 31380 where you can access the MLflow UI.


Reference: Object storage credentials
-------------------------------------

Charmed MLflow uses `MinIO <https://charmhub.io/minio>`_ as the object storage. 
Get your credentials by running the following command:

.. code-block:: bash

   juju run mlflow-server/0 get-minio-credentials

This action returns ``secret-key`` and ``secret-access-key``.
