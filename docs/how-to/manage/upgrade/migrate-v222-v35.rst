Upgrade from 2.22 to 3.5
=========================

This guide describes how to upgrade Charmed MLflow from version 2.22 to 3.5. 

Requirements
-------------

* You have deployed MLflow version 2.22.
* You have Command Line Interface (CLI) access to the machine where the Juju controller is deployed. All commands in this guide are executed from it.

.. tip:: 
    Before proceeding, you might want to backup MinIO data including your experiments and models. See :ref:`backup` for more details.

Upgrade dependencies
---------------------

Charmed MLflow 3.5 requires:

1. `MicroK8s <https://microk8s.io/>`_ version 1.29 or higher.
2. `Juju <https://juju.is/>`_ version 3.6.

If you do not meet these requirements, please upgrade these dependencies. 
See `MicroK8s upgrade <https://microk8s.io/docs/upgrading>`_ 
and `Juju upgrade <https://documentation.ubuntu.com/juju/3.6/howto/manage-your-juju-deployment/upgrade-your-juju-deployment/#upgrade-your-deployment>`_ respectively for more details.

Upgrade MLflow
---------------

To upgrade MLflow from 2.22 to 3.5, run the following commands:

.. code-block:: bash

    juju refresh mlflow-minio --channel=ckf-1.10/stable
    juju refresh mlflow-server --channel=3.5/stable

.. note::
    MLflow 3.5 is a major version upgrade from 2.22. Please review the `MLflow 3.x release notes <https://github.com/mlflow/mlflow/releases>`_ for any breaking changes that may affect your workflows.
