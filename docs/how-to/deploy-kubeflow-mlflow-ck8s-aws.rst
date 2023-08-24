Integrating Charmed MLflow with Charmed Kubeflow v2 on Charmed Kubernetes
========================================================

Welcome to the guide on how to integrate Charmed MLflow on `Charmed Kubernetes <https://ubuntu.com/kubernetes/charmed-k8s>`_. In this guide, we will guide you through the process of integrating charme MLflow with Charmed Kubeflow on Charmed Kubernetes.

Prerequisites
--------------
We assume that:

* You have access to a Charmed Kubernetes cluster using ``kubectl``. If you don't have a cluster set up, you can follow `this guide <https://discourse.charmhub.io/t/create-a-charmed-kubernetes-cluster-for-use-with-an-mlops-platform-on-aws/11634>`_ to deploy one on AWS.
* You have deployed charmed Kubeflow bundle. If you don't have here is a guide on how to do it.
* You have deployed charmed MLflow bundle. If you don't have here is a guide on how to do it.


Deploy resource dispatcher
--------------------------

Deploy resource dispatcher:

.. code-block:: bash

   juju deploy resource-dispatcher --trust

Relate Resource dispatcher to MLflow
------------------------------------

Relate Resource dispatcher to MLflow:

.. code-block:: bash

   juju relate mlflow-server:secrets resource-dispatcher:secrets
   juju relate mlflow-server:pod-defaults resource-dispatcher:pod-defaults

Integrate MLflow with Kubeflow notebook
---------------------------------------

Please reffer to this doc: :ref:`my-reference-label`.
