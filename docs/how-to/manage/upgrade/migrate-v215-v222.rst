Upgrade from 2.15 to 2.22
=========================

This guide describes how to upgrade Charmed MLflow from version 2.15 to 2.22. 

Requirements
-------------

* You have deployed MLflow version 2.15.
* You have Command Line Interface (CLI) access to the machine where the Juju controller is deployed. All commands in this guide are executed from it.

.. warning:: 
    Before proceeding, we strongly recommend you back up all your MLflow data, meaning both your backend store and your artifact store, in case anything goes unexpectedly wrong and data is lost. See :ref:`backup` for more details.

Upgrade dependencies
---------------------

Charmed MLflow 2.22 requires:

1. `MicroK8s <https://microk8s.io/>`_ version 1.29 or higher.
2. `Juju <https://juju.is/>`_ version 3.6.

If you do not meet these requirements, please upgrade these dependencies. 
See `MicroK8s upgrade <https://microk8s.io/docs/upgrading>`_ 
and `Juju upgrade <https://documentation.ubuntu.com/juju/3.6/howto/manage-your-juju-deployment/upgrade-your-juju-deployment/#upgrade-your-deployment>`_ respectively for more details.

Upgrade MLflow
---------------

To upgrade MLflow from 2.15 to 2.22, run the following commands:

.. code-block:: bash

    juju refresh mlflow-minio --channel=ckf-1.10/stable
    juju refresh mlflow-server --channel=2.22/stable

After the `mlflow-server` charm upgrade, the tracking server is expected not to be able to start, because database migrations are to be run after the schema change that comes with the tracking server's upgrade, for the new version of the application to start successfully. While the newer Charmed MLflow 3 takes care of running database migrations for you on charm upgrades, Charmed MLflow 2 does not and you are expected to manually migrate the database. To achieve that:

1. Connect to one of the containers of the tracking server, for example to the one from pod `mlflow-server-0` in the `kubeflow` namespace:

   .. code-block:: bash

       kubectl exec -it -n kubeflow pods/mlflow-server-0 -c mlflow-server -- /bin/bash

2. Get the backend store URI, for example by reading the corresponding environment variable set by the charm for the tracking server:

   .. code-block:: bash

       pebble exec --context mlflow-server -- env | grep MLFLOW_BACKEND_STORE_URI

3. Run database migrations via `MLflow's own CLI utility <https://mlflow.org/docs/2.22.1/api_reference/cli.html#mlflow-db-upgrade>`:

   .. code-block:: bash

       mlflow db upgrade <your-backend-store-uri>
