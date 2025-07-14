Integrate with COS
===================

This guide shows how to integrate Charmed MLflow with the `Canonical Observability Stack (COS) <https://charmhub.io/topics/canonical-observability-stack>`_.

Requirements
-------------

#. You have deployed the COS stack in the ``cos`` model. For steps on how to do this, see the `MicroK8s tutorial <https://charmhub.io/topics/canonical-observability-stack/tutorials/install-microk8s>`_.
#. You have deployed the MLflow bundle in the ``kubeflow`` model. For steps on how to do this, see :ref:`tutorial_get_started`.

.. note::
    As per COS best practices, this guide assumes that COS and MLflow are deployed each using their own controllers. 
    This means that after the deployment, there is a ``kubeflow`` and a ``cos`` model associated with ``kf-controller`` and ``cos-controller`` controllers, respectively. 
    These are the default names for the controllers. Users can set any other name during the controller bootstrapping.

Deploy Grafana agent
--------------------

Deploy the `Grafana agent <https://charmhub.io/grafana-agent-k8s>`_ to your ``kubeflow`` model alongside the MLflow bundle. Run the following command:

.. code-block:: bash

    juju deploy grafana-agent-k8s --channel=edge --trust

Relate MLflow Server Prometheus Metrics to Grafana Agent
--------------------------------------------------------

Establish the relationship between the MLflow Server Prometheus metrics and the Grafana Agent. Use the following command:

.. code-block:: bash

    juju add-relation mlflow-server:metrics-endpoint grafana-agent-k8s:metrics-endpoint

Relate Grafana Agent to Prometheus in the COS Model
---------------------------------------------------

Consume offers in Kubeflow
--------------------------

Within the ```kubeflow``` model, you can consume COS offers for Prometheus, Grafana and Loki as follows:

.. code-block:: bash

    juju add-relation grafana-agent-k8s admin/cos.prometheus-receive-remote-write

Relate MLflow Server in the Kubeflow Model to Grafana Charm in the COS Model
----------------------------------------------------------------------------

Establish the relationship between the MLflow Server in the ``kubeflow`` model and the Grafana charm in the ``cos`` model. Run the following command:

.. code-block:: bash

    juju add-relation mlflow-server admin/cos.grafana-dashboards

Obtain the Grafana dashboard admin password
-------------------------------------------

Switch the model to ``cos`` and retrieve the Grafana dashboard admin password. 
Execute the following commands:

.. code-block:: bash

    juju switch cos
    juju run-action grafana/0 get-admin-password --wait

Obtain the Grafana dashboard URL
--------------------------------

To access the Grafana dashboard, you need the URL. 
Run the following command to get the URLs for COS endpoints:

.. code-block:: bash

    juju show-unit catalogue/0 | grep url

You will see a list of endpoints similar to the following:

.. code-block:: bash

    url: http://10.43.8.34:80/cos-catalogue
    url: http://10.43.8.34/cos-grafana
    url: http://10.43.8.34:80/cos-prometheus-0
    url: http://10.43.8.34:80/cos-alertmanager

Choose the ``cos-grafana`` URL and access it in your browser.

Login to Grafana
----------------

Login to Grafana with the password obtained from the previous section. The username is ``admin``.

Access the dashboard in the UI
------------------------------

Go to the left sidebar and choose the MLflow Dashboards from the list. From the General dashboards folder choose the ``MLflow metrics Dashboard``. When accessing the dashboard for the first time, choose some reasonable time range from the top right dropdown.
