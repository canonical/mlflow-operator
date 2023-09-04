Integrate MLflow with Jupyter Notebook
======================================

This guide shows how to integrate Charmed MLflow with Jupyter Notebook. It assumes you are running locally, on your workstation. 

Prerequisites
-------------

This guide assumes the following:

#. You have deployed MLflow. 
#. You have CLI access to the machine where the Juju controller is deployed (all commands will be executed from there).

Install and prepare Jupyter Lab
-------------------------------

Install JupyterLab:

.. code-block:: bash

   pip install jupyterlab


Run JupyterLab:

.. code-block:: bash

   jupyter lab

