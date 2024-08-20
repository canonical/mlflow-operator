Upgrade Charmed MLflow 2.1 to 2.15
==================================================

This guide describes how to upgrade Charmed MLflow version 2.1 to 2.15. 

Prerequisites
-------------

This guide assumes:

* You have deployed MLflow version 2.11.
* You have Command Line Interface (CLI) access to the machine where the Juju controller is deployed. All commands in this guide are executed from it.

.. tip:: 
    Before proceeding, you might want to backup MinIO data including your experiments and models. See :ref:`backup` for more details.

Upgrade dependencies
---------------------

Charmed MLflow 2.15 requires:

1. `MicroK8s <https://microk8s.io/>`_ version 1.29 or higher.
2. `Juju <https://juju.is/>`_ version 3.4.

If you do not meet these requirements, please upgrade these dependencies. 
See `MicroK8s upgrade <https://microk8s.io/docs/upgrading>`_ 
and `Juju upgrade <https://juju.is/docs/juju/upgrade-your-juju-deployment>`_ respectively for more details.

Upgrade MLflow bundle
----------------------

To upgrade the MLflow bundle charms from 2.11 to 2.15, run the following commands:

.. code-block:: bash

    juju refresh mlflow-minio --channel=ckf-1.9/stable
    juju refresh mlflow-server --channel=2.15/stable

Upgrade resource dispatcher
--------------------------------------

Only if you are running MLflow within Kubeflow, you must upgrade your `resource dispatcher <https://github.com/canonical/resource-dispatcher>`_ deployment. 

.. note::
    MLflow 2.15 works only with resource dispatcher version 2.0/stable.

To upgrade your resource dispatcher, do the following:

.. code-block:: bash

    juju refresh resource-dispatcher --channel=2.0/stable
