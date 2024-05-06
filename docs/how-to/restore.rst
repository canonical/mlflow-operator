.. _restore:

Restore MLflow data
===================

The following instructions will allow you to restore the Charmed MLflow
control plane data from a compatible S3 storage.

Pre-requisites
--------------

1. Access to a S3 storage - only AWS S3 and S3 RadosGW are supported
2. Admin access to the Kubernetes cluster where Charmed MLflow is deployed
3. Juju admin access to the `mlflow` model
4. `rclone`_ installed and `configured`_ to connect to the S3 storage from 1
5. `s3-integrator` deployed and configured

   1. https://charmhub.io/mysql-k8s/docs/h-configure-s3-aws

   2.   https://charmhub.io/mysql-k8s/docs/h-configure-s3-radosgw

6. `yq binary`_

.. note:: This S3 storage will be used for storing all backup data from MLflow.

Throughout the following guide we’ll use the following ENV vars in the commands

.. code-block:: bash

   S3_BUCKET=backup-bucket-2024
   RCLONE_S3_REMOTE=remote-s3
   RCLONE_MINIO_MLFLOW_REMOTE=minio-mlflow
   RCLONE_BWIDTH_LIMIT=20M

Through the guide we’ll be using rclone to both get files from MinIO and push
the backup to an S3 endpoint. An example configuration looks like this:

.. code-block::

   [minio-mlflow]
   type = s3
   provider = Minio
   access_key_id = minio
   secret_access_key = ...
   endpoint = http://localhost:9000
   acl = private

.. note:: You can check where this configuration file is located with `rclone config file`

Restore DB from S3
------------------

1. Scale up `mlflow-mysql`:
^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. warning:: In a single node setup, the `Primary` database will become unavailable during the backup. It is recommended to have a multinode setup before backing up the data.

.. code-block:: bash

   juju scale-application mlflow-mysql 2


2. Restore MySQL
^^^^^^^^^^^^^^^^

.. note:: Please replace `mysql-k8s` with the name of the database you intend to create a backup for in the commands form that guide. E.g. `mlflow-mysql` instead of `mysql-k8s`.

Restore `mlflow` MinIO bucket
-----------------------------

.. note:: The name of the MLflow MinIO bucket defaults to `mlflow`, the bucket name can be verified with `juju config mlflow default_artifact_root`.

1. Configure `rclone` for MinIO
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
You can use this sample `rclone` configuration as a reference:

.. code-block::

   [minio-mlflow]
   type = s3
   provider = Minio
   access_key_id = minio
   secret_access_key = ...
   endpoint = http://localhost:9000
   acl = private

Note that the machine will need to use a URL to access MinIO. In this case we'll use kubectl to do a port forward:

.. code:: bash

   kubectl port-forward -n kubeflow svc/mlflow-minio 9000:9000

.. note::

   In order to find the `secret-access-key` for MinIO you'll need to run the following command:

   .. code:: bash

      juju show-unit mlflow-server/0 \
          | yq '.mlflow-server/0.relation-info.[] | select (.related-endpoint == "object-storage") | .application-data.data' \
          | yq '.secret-key'

In the future the MinIO Charm will be extended so that it can send it's data directly to the S3 endpoint.


2. Sync buckets from S3 to MinIO
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code:: bash

  rclone --size-only sync \
    --bwlimit $RCLONE_BWIDTH_LIMIT \
    $RCLONE_S3_REMOTE:$S3_BUCKET/mlflow \
    $RCLONE_MINIO_MLFLOW_REMOTE:mlflow

Next Steps
----------

* Want to create a backup of MLflow's data? See :ref:`backup`
