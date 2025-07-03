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

    juju deploy -m kubeflow grafana-agent-k8s --channel=stable --trust

Make offers from COS
--------------------

You can make `offers <https://documentation.ubuntu.com/juju/3.6/reference/offer/>`_ for Prometheus, Grafana and Loki from COS as follows:

.. code-block:: bash

    juju offer -c cos-controller cos.prometheus:receive-remote-write prometheus-receive-remote-write
    juju offer -c cos-controller cos.grafana:grafana-dashboard grafana-dashboards
    juju offer -c cos-controller cos.loki:logging loki-logging

.. note:: If you've deployed COS with `offers` overlay, making offers is not necessary, since they already exist.

Consume offers in Kubeflow
--------------------------

Within the ```kubeflow``` model, you can consume COS offers for Prometheus, Grafana and Loki as follows:

.. code-block:: bash

    juju consume -m kf-controller:kubeflow cos-controller:cos.prometheus-receive-remote-write
    juju consume -m kf-controller:kubeflow cos-controller:cos.grafana-dashboards
    juju consume -m kf-controller:kubeflow cos-controller:cos.loki-logging

Connect Grafana agent to endpoints
----------------------------------

The Grafana agent can provide metrics, alerts, dashboards and logs to COS via these three relation endpoints:

* `send-remote-write <https://charmhub.io/grafana-agent-k8s/integrations#send-remote-write>`_
* `grafana-dashboards-provider <https://charmhub.io/grafana-agent-k8s/integrations#grafana-dashboards-provider>`_
* `logging-provider <https://charmhub.io/grafana-agent-k8s/integrations#logging-provider>`_

You can tell the Grafana agent to provide those by consuming those offers as follows:

.. code-block:: bash

    juju integrate -m kf-controller:kubeflow grafana-agent-k8s:send-remote-write prometheus-receive-remote-write
    juju integrate -m kf-controller:kubeflow grafana-agent-k8s:grafana-dashboards-provider grafana-dashboards
    juju integrate -m kf-controller:kubeflow grafana-agent-k8s:logging-consumer loki-logging

Verify the relations for all offers are in place:

.. code-block:: bash

    juju status -m cos-controller:cos grafana-agent-k8s --relations

Integrate with Prometheus
-------------------------

You can provide charms metrics to Prometheus in COS by linking the MLflow charms to the `metrics-endpoint` as follows:

.. code-block:: bash

    juju integrate minio:metrics-endpoint grafana-agent-k8s:metrics-endpoint
    juju integrate mlflow-mysql:metrics-endpoint grafana-agent-k8s:metrics-endpoint
    juju integrate mlflow-server:metrics-endpoint grafana-agent-k8s:metrics-endpoint

Integrate with Grafana
------------------------
You can link MLflow charms to the Grafana agent via the ``grafana-dashboards-consumer`` endpoint in COS as follows:

.. code-block:: bash

    juju integrate minio:grafana-dashboard grafana-agent-k8s:grafana-dashboards-consumer
    juju integrate mlflow-mysql:grafana-dashboard grafana-agent-k8s:grafana-dashboards-consumer
    juju integrate mlflow-server:grafana-dashboard grafana-agent-k8s:grafana-dashboards-consumer

Integrate with Loki
-------------------
You can provide charm logs to Loki in COS by integrating the MLflow charms with ``loki-logging`` endpoint and Grafana agent as follows:

.. code-block:: bash

    juju integrate mlflow-mysql:logging grafana-agent-k8s:logging-provider
    juju integrate mlflow-server:logging grafana-agent-k8s:logging-provider

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

See the available dashboards by clicking on Dashboards in the sidebar menu. When accessing the dashboard for the first time, choose some reasonable time range from the top right dropdown.
