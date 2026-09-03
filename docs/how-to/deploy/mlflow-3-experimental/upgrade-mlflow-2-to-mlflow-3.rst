Upgrade an Existing MLflow 2 Deployment to MLflow 3
===================================================

This guide describes how to upgrade an existing Charmed MLflow 2 deployment to the experimental Charmed MLflow 3.

During the upgrade, the tracking server automatically migrates its backend database schema to the new version. No manual migration step is required, but the relation to the backend database must be recreated so that the upgraded charm can obtain the privileges the migration needs (see :ref:`Upgrade MLflow <upgrade_mlflow>` below).

.. warning::
    MLflow 3 support is **experimental** and is not intended for production workloads.

Review the migration implications
---------------------------------

MLflow 3 introduces breaking changes compared to MLflow 2, including removed features, changed APIs, and a different storage layout for model artifacts. The migration is also effectively one-way: MLflow 3 can read experiments, runs, and models created with MLflow 2, but MLflow 2 cannot read resources once they have been migrated to MLflow 3.

Before upgrading, review the upstream documentation to understand and accept these implications for your experiments, models, and client code:

* The `MLflow 3 migration guide <https://mlflow.org/docs/latest/ml/mlflow-3/>`_ describes the breaking changes and API updates between MLflow 2.x and 3.x.
* The `upgrade guide for self-hosted MLflow servers <https://mlflow.org/docs/latest/self-hosting/migration/>`_ provides background on the server-side upgrade and backend database schema migration. Charmed MLflow performs these steps automatically for you, so you do not need to run them manually.

.. warning::
    This upgrade is not reversible. Once the backend database schema is migrated to MLflow 3, you cannot downgrade the tracking server back to MLflow 2 without restoring a database backup taken beforehand (see :ref:`Back up the backend database <backup_backend_database>` below). Make sure your workflows and client code are compatible with MLflow 3 before proceeding.

Requirements
-------------

* You have an existing Charmed MLflow 2.22 deployment. If you are running an older 2.X version, first upgrade it to 2.22 by following the :doc:`stable upgrade guides <../../manage/upgrade/index>`.
* You have Command Line Interface (CLI) access to the machine where the Juju controller is deployed. All commands in this guide are executed from it.
* Your deployment uses ``mysql-k8s`` as its backend store, related to ``mlflow-server`` over the ``backend-store-db`` endpoint.

.. _backup_backend_database:

Back up the backend database
----------------------------

Charmed MLflow uses MySQL as its backend store, where the metadata of all experiments, runs, parameters, and metrics is kept.

The upgrade migrates the MySQL schema to the new MLflow version. These migrations are **not atomic**: they are applied as a sequence of statements that is not wrapped in a single transaction, and they are not automatically reversible. If a migration fails partway through, the database can be left in an inconsistent, partially-migrated state that cannot be rolled back automatically.

.. warning::
    Before proceeding, back up the backend database. If the migration fails, restoring this backup is the only way to recover the state you had before the upgrade.

    Because the backend store is MySQL, see the `MySQL backup and restore guide <https://canonical.com/data/mysql/docs/8.0/how-to/back-up-and-restore/>`_ for how to back up your database.

.. _upgrade_mlflow:

Upgrade MLflow
---------------

.. note::
    To complete the schema migration, the upgraded charm needs elevated database privileges, but Juju only grants them when the database relation is first created. Because the relation already exists from your MLflow 2 deployment, you must remove it and add it back so the privileges are granted to the upgraded charm. This step is a temporary workaround for `this upstream issue <https://github.com/mlflow/mlflow/issues/19943>`_.

First, remove the relation between the tracking server and the backend database:

.. code-block:: bash

    juju remove-relation mlflow-server:backend-store-db mysql-k8s:database

This leaves ``mlflow-server`` without a backend store. Wait until ``mlflow-server`` reports a ``blocked`` status and ``mysql-k8s`` settles back to ``active``. You can monitor the statuses with:

.. code-block:: bash

    juju status --watch 5s

Next, refresh the tracking server to the experimental MLflow 3 channel:

.. code-block:: bash

    juju refresh mlflow-server --channel=3.15/edge

Wait until ``mlflow-server`` reports a ``blocked`` status again, as it is still waiting for its backend store.

Finally, re-establish the relation with the backend database:

.. code-block:: bash

    juju integrate mysql-k8s:database mlflow-server:backend-store-db

The tracking server now migrates the backend database schema automatically and becomes ``active`` once the migration completes. You can watch its progress with:

.. code-block:: bash

    juju status --watch 5s

When ``mlflow-server`` reaches an ``active`` status, the schema migration has succeeded and the upgrade is complete. Your pre-upgrade experiments, runs, parameters, and metrics are preserved, because removing the relation deletes only the scoped database user, not the database itself.
