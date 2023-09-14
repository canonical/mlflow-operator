Integrate Charmed MLflow with Charmed Kubeflow on Charmed Kubernetes
=========================================================================

+------------+---------+
| Component  | Version |
+============+=========+
| MLflow     | 2       |
+------------+---------+

In this guide, we will guide you through the process of integrating Charmed MLflow with Charmed Kubeflow on `Charmed Kubernetes <https://ubuntu.com/kubernetes/charmed-k8s>`_.

Prerequisites
--------------
We assume that:

* You have access to a Charmed Kubernetes cluster using ``kubectl``. If you don't have a cluster set up, you can follow the :doc:`creation guide <create-ck8s-aws>` to deploy one on AWS.
* You have deployed the Charmed Kubeflow bundle. If you don't have it, here is `a guide <https://discourse.charmhub.io/t/deploying-charmed-kubeflow-to-charmed-kubernetes-on-aws/11667>`_ on how to do it.
* You have deployed the Charmed MLflow bundle. To see how, follow our :doc:`deployment guide <deploy-ck8s-aws>`.

Deploy resource dispatcher
--------------------------

Deploy the resource dispatcher:

.. code-block:: bash

   juju deploy resource-dispatcher --channel 1.0/stabe --trust

Relate Resource dispatcher to MLflow
------------------------------------

Relate the Resource dispatcher to MLflow:

.. code-block:: bash

   juju relate mlflow-server:secrets resource-dispatcher:secrets
   juju relate mlflow-server:pod-defaults resource-dispatcher:pod-defaults

Integrate MLflow with Kubeflow notebook
---------------------------------------

Please refer to this doc: :doc:`../tutorial/mlflow-kubeflow`.