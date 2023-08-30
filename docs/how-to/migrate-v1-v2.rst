Migrate Charmed MLflow Version 1 to Version 2
=====================================================

This guide shows how to migrate Charmed MLflow version 1 to version 2. This guide assumes you are running the old Charmed MLflow stack version 1, which runs with MariaDB. With MLflow version 2, we only support the MySQL integration. This guide outlines how to move data from MariaDB to MySQL and how to migrate data from version 1 to version 2.1.1. Data from the object store doesn't need to be migrated.

Prerequisites
-------------

This guide assumes the following:

#. You have deployed MLflow version 1 with MariaDB, MLflow server version 1.x, and MinIO.
#. You have CLI access to the machine where the Juju controller is deployed (all commands will be executed from there).

MariaDB Backup
--------------

Install the ``mysqldump`` command:

.. code-block:: bash

   sudo apt update
   sudo apt install mysql-client

Backup the MariaDB database with the following command:

.. code-block:: bash

   mysqldump --host=<mariadb-charm-ip-address> --user=root --password=root --column-statistics=0 --databases database > mlflow-db.sql

Deploy MySQL Charm
-------------------

Deploy the MySQL charm, which is needed for MLflow v2:

.. code-block:: bash

   juju deploy mysql-k8s --channel 8.0/beta --series jammy --trust

.. note:: For MLflow version ``v.2.1``, we deploy the 8.0/beta version of the charm. You may deploy a more up to date version in your case.

Please wait until the charm goes to active in ``juju status``. Then run the following command to get the password for MySQL:

.. code-block:: bash

   juju run-action mysql-k8s/0 get-password --wait

Adjust the Database Backup
--------------------------

Rename the database from ``database`` (used in MariaDB) to ``mlflow`` (used in MySQL):

.. code-block:: bash

   sed 's/`database`/`mlflow`/g' mlflow-db.sql > mlflow-db-updated.sql

Rename one duplicate constraint as MySQL does not allow that:

.. code-block:: bash

   sed -i '0,/`CONSTRAINT_1`/s//`CONSTRAINT-1`/' mlflow-db-updated.sql

You can do all the above modifications in the text editor of your choice if you prefer.

Move Database to MySQL
----------------------

Install the MySQL CLI tool:

.. code-block:: bash

   sudo apt update
   sudo apt-get install mysql-shell

Connect to the MySQL charm:

.. code-block:: bash

   mysql --user=root --host=<mysql-unit-ip> -p
   # you will be prompted for password

Create the MySQL database called ``mlflow``:

.. code-block:: bash

   CREATE DATABASE mlflow;

Leave the client with ``ctrl + D``.

Move the updated database dump file to MySQL:

.. code-block:: bash

   mysql -u root -p <mysql_password> mlflow <mlflow-db-updated.sql

Migrate MySQL Database
----------------------

Install the MLflow Python client version 2.1.1:

.. code-block:: bash

   pip install mlflow==2.1.1

Run the migration script against the MySQL ``mlflow`` database:

.. code-block:: bash

   mlflow db upgrade mysql+pymysql://root:<mysql-password>@<mysql-ip>/mlflow

Update MLflow Server
---------------------

Remove relations from the old MLflow server:

.. code-block:: bash

   juju remove-relation mlflow-db:mysql mlflow-server:db
   juju remove-relation minio mlflow-server

Update the MLflow server:

.. code-block:: bash

   juju refresh mlflow-server --channel 2.1/edge

Create relations with MinIO and MySQL:

.. code-block:: bash

   juju relate mysql-k8s mlflow-server
   juju relate minio mlflow-server
