Migrate Charmed MLflow Version 2.1 to Version 2.15
==================================================

This guide shows how to migrate Charmed MLflow version 2.1 to version 2.15. This guide assumes you are running the old Charmed MLflow stack version 2.11.

Prerequisites
-------------

This guide assumes the following:

#. You have deployed MLflow version 2.11.
#. You have a CLI access to the machine where the Juju controller is deployed (all commands will be executed from there).

Backup data (Optional)
----------------------

Although the process of upgrading MLflow 2.11 to 2.15 should be seamless you might still want to backup your MinIO data with your experiments and models. You can find all the details in the :ref:`backup` how to guide.

.. note:: We don't have to backup the MySQL data as we are still using the same version `8.0/stable` in both versions of MLflow.

Update MLflow bundle
--------------------

To update MLflow bundle components from 2.11 to 2.15 please run these commands.

.. code-block:: bash

    juju refresh mlflow-minio --channel=ckf-1.9/stable --trust
    juju refresh mlflow-mysql --channel=8.0/stable --trust 
    juju refresh mlflow-server --channel=2.15/stable --trust 