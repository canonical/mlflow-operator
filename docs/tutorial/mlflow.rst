.. _tutorial_get_started:

Get started with Charmed MLflow
==================================

`MLflow <https://mlflow.org/>`_ is an open-source platform, used for managing machine learning workflows. It has four primary functions that include experiment tracking, model registry, model management and code reproducibility.

Charmed MLflow is a `charm bundle <https://canonical-juju.readthedocs-hosted.com/en/latest/user/reference/bundle/>`_ that enables the deployment of MLflow quickly and easily with just a few commands.

This tutorial describes how to deploy Charmed MLflow using the `Juju <https://juju.is/>`_ CLI tool and a local `MicroK8s <https://microk8s.io/>`_ cloud.

Requirements
-------------

* Ubuntu 22.04 or later.
* A host machine with at least 50GB of disk space available.

Install and prepare MicroK8s
----------------------------
MicroK8s can be installed from a snap package. The published snap maintains different ``channels`` for different releases of Kubernetes.

.. code-block:: bash

   sudo snap install microk8s --channel=1.32/stable --classic

For MicroK8s to work without having to use ``sudo`` for every command, it creates a group called ``microk8s``. To make it more convenient to run commands, add the current user to this group:

.. code-block:: bash

   sudo usermod -a -G microk8s $USER
   newgrp microk8s

For deploying Charmed MLflow, additional features from the default ones that come with MicroK8s are needed. These can be installed as MicroK8s add-ons. Run the following command to enable them:

.. code-block:: bash
	sudo microk8s enable dns hostpath-storage metallb:10.64.140.43-10.64.140.49 rbac

> See More : `MicroK8s | How to use addons <https://microk8s.io/docs/addons>`_

To confirm that all add-ons are successfully enabled, check the MicroK8s status as follows:

.. code-block:: bash
	microk8s status

.. note:: The add-ons configuration may take a few minutes to complete before they are listed as enabled.


Install Juju
------------
`Juju <https://juju.is/>`_ is an operation Lifecycle manager (OLM) for clouds, bare metal or Kubernetes. We will be using it to deploy and manage the components which make up Kubeflow.

To install Juju from snap, run this command:

.. code-block:: bash

   sudo snap install juju --channel=3.6/stable

On some machines there might be a missing folder which is required for Juju to run correctly. To ensure that this folder exists, run:

.. code-block:: bash
   
   mkdir -p ~/.local/share

As a next step, configure MicroK8s to work properly with Juju by running:

.. code-block:: bash

   microk8s config | juju add-k8s my-k8s --client

Now, run the following command to deploy a Juju controller to the Kubernetes we set up with MicroK8s:

.. code-block:: bash

   juju bootstrap microk8s

.. note:: The controller may take a few minutes to deploy.

The controller is the agent of Juju, running on Kubernetes, which can be used to deploy and control the MLflow components.

Next, we'll need to add a model for Kubeflow to the controller. Run the following command to add a model called ``kubeflow``:

.. code-block:: bash

   juju add-model kubeflow

.. note:: The model name here can be anything. In this tutorial, ``kubeflow`` is being used because you may want to deploy MLflow along with Kubeflow, and in that case, the model name must be ``kubeflow``.


Deploy MLflow bundle
--------------------
MicroK8s uses inotify to interact with the file system. Large Microk8s deployment sometimes exceed the default ``inotify`` limits. To increase the limits, run the following commands:

.. code-block:: bash

   sudo sysctl fs.inotify.max_user_instances=1280
   sudo sysctl fs.inotify.max_user_watches=655360

If you want these commands to persist across machine restarts, add these lines to ``/etc/sysctl.conf``:

.. code-block:: bash
				
	fs.inotify.max_user_instances=1280
	fs.inotify.max_user_watches=655360
   

To deploy the MLflow bundle, run the following command:

.. code-block:: bash

   juju deploy mlflow --channel=2.22/stable --trust

This deploys the stable version of MLflow with `MinIO <https://min.io/>`_ as the object storage and `MySQL` as the metadata store.

Once the deployment is completed, you will see a message such as the following:

.. code-block:: bash
   
   Deploy of bundle completed.

You can use the following command to check the status of all the model components:

.. code-block:: bash

   juju status

The deployment is ready when the statuses of all the applications and the units in the bundle have an active status. You can also use the ``watch`` option to continuously watch the status of the model:

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


Reference: Object storage credentials
-------------------------------------
To use MLflow you need to have credentials to the object storage. The aforementioned bundle comes with MinIO. To get the ``MinIO`` credentials run the following command:

.. code-block:: bash

   juju run mlflow-server/0 get-minio-credentials

This action will output ``secret-key`` and ``secret-access-key``.
