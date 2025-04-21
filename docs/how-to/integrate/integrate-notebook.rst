Integrate with Jupyter Notebooks
==================================

To run Jupyter Notebooks in Charmed MLflow, JupyterLab must be deployed and a number of configurations made.

Prerequisites
-------------

- You are deploying Jupyter Notebook and MLflow on a workstation running Ubuntu 20.04 (focal) or later.
- Your workstation has at least 4 cores, 32GB RAM, and 32GB of disk space available.
- Your workstation is connected to the internet for downloading the required snaps and charms.

Deploy MLflow
-------------

Follow the steps in this tutorial to deploy MLflow on your VM: :doc:`../tutorial/mlflow`. Confirm that you can now access the MLflow UI on ``http://localhost:31380.``

Deploy JupyterLab
-----------------

Install JupyterLab:

.. code-block:: bash

   pip install jupyterlab

Run JupyterLab:

.. code-block:: bash

   jupyter lab

Access MLflow UI
----------------

Access the MLflow UI:

.. code-block:: bash

   mlflow ui

Configure MinIO and MLflow
--------------------------

Before you can run your first experiment, there are a couple of things to adjust — the MLflow URI and the MinIO URI. To do this:

#. Open a new terminal window connected to the instance you have been using.

#. Enter the following command to check the status:

   .. code-block:: bash

      juju status

#. Now, go back to the Notebook and update the MLflow URL and MinIO URL as needed.

#. Once those are updated, there is one last step you need to do. Return to the terminal and run:

   .. code-block:: bash

      juju run-action mlflow-server/0 get-minio-credentials — wait

   This will display the secret-key and secret-access-key. Be sure to update them in the Notebook as well.

Now, you are ready to run your first experiment. After finalising the run, you can go to the MLflow UI and view the experiment results.

