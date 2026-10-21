.. _deploy_mlflow_3_with_terraform:

Deploy Charmed MLflow 3 with Terraform (Experimental)
=====================================================

.. warning::

    MLflow 3 support is **experimental** and is not intended for production workloads. ``latest/edge`` charm channels are employed, since Charmed MLflow 3 is only available there as of today, and such channels are under active development and may change at any time.

This guide describes how to deploy Charmed MLflow 3 (experimental) using `Terraform <https://www.terraform.io/>`_, as its own standalone product, via the Charmed Kubeflow Terraform solutions.

.. note::

    Despite the name of the Terraform solutions, this guide does not deploy any component of Charmed Kubeflow, but only Charmed MLflow together with:

    * The components the MLflow application needs:

      * its backend store (a PostgreSQL database)
      * its artifact store (an S3-compatible storage integrator)

    * The necessary charm ecosystem around the MLflow charm:

      * a service mesh based on Istio in ambient mode
      * an identity and access management layer based on the `Canonical Identity Platform <https://canonical-identity.readthedocs-hosted.com/>`_ and OAuth2 Proxy


Requirements
-------------

* `Terraform <https://developer.hashicorp.com/terraform/install>`__ version 1.6 or later
* `Git <https://git-scm.com/>`_ to download the Terraform source
* A Kubernetes cluster:
    * of version 1.32, 1.33 or 1.34
    * preconfigured to work with Istio (e.g., on Canonical K8s, `configure Cilium accordingly <https://docs.cilium.io/en/latest/network/servicemesh/istio/#cilium-configuration>`_)
    * with at least three external IP addresses available for ingress load balancers
* The following environment variables set as desired for DNS of external IP addresses:
    * ``AUTH_HOSTNAME`` for the hostname to be employed to authenticate to MLflow (e.g., ``auth.mlflow.com``)
    * ``API_HOSTNAME`` for the hostname to be employed to reach MLflow's API for external, machine-to-machine access (e.g., ``api.mlflow.com``)
    * ``UI_HOSTNAME`` for the hostname to be employed to reach MLflow's user interface (e.g., ``ui.mlflow.com``)
* Some S3-compatible storage bucket preconfigured, and the following environment variables set accordingly on your CLI:
    * ``S3_ENDPOINT`` for the endpoint
    * ``S3_ACCESS_KEY`` for the access key
    * ``S3_SECRET_KEY`` for the secret key
    * ``S3_BUCKET_NAME`` for the bucket name
* The `Juju <https://juju.is/>`_ client installed, with a controller bootstrapped on your Kubernetes cloud - the Terraform Juju provider operates on the controller your Juju client is currently connected to


Deploy Charmed MLflow 3 and Its Charm Ecosystem
-----------------------------------------------

1. Download the source of the Terraform solution by cloning the repository
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    git clone https://github.com/canonical/charmed-kubeflow-solutions.git

2. Move into the directory of the Terraform solution for Charmed MLflow 3 with ambient-mode Istio and Identity and Access Management (IAM)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    cd charmed-kubeflow-solutions/terraform/tests/mlflow-ambient-iam

3. Initialize Terraform to download provider and modules
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    terraform init

4. Deploy the whole solution
^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    terraform apply -auto-approve -parallelism=1 \
        -var create_model=true \
        -var external_auth_hostname=$AUTH_HOSTNAME \
        -var external_m2m_hostname=$API_HOSTNAME \
        -var external_ui_hostname=$UI_HOSTNAME \
        -var s3_endpoint_global=$S3_ENDPOINT \
        -var s3_access_key_global=$S3_ACCESS_KEY \
        -var s3_secret_key_global=$S3_SECRET_KEY \
        -var s3_bucket_global=$S3_BUCKET_NAME

.. note::

    With this command, ``latest/edge`` channels are selected by default, which is necessary because Charmed MLflow 3 is only available there as of today.

Terraform will create four different Juju models across which all the charm applications will be deployed and integrated:  ``istio-system``, ``iam-core``, ``iam`` and ``mlflow``.

5. Wait for all of the charm applications in the ``iam-core`` model to become ``active``
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    juju status --model iam-core --watch 5s

6. Add your hostnames to the internal DNS of Kubernetes and to the DNS of your host
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. note::

    The hostnames set above do not have to be registered with a public DNS provider if (and only if) the following steps are applied.

Find the IP addresses that correspond to your hostnames from the deployed load balancers:

.. code-block:: bash

    AUTH_IP=$(kubectl -n iam-core get svc traefik-lb -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
    API_IP=$(kubectl -n mlflow get svc istio-ingress-k8s-m2m-istio -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
    UI_IP=$(kubectl -n mlflow get svc istio-ingress-k8s-ui-istio -o jsonpath='{.status.loadBalancer.ingress[0].ip}')

For Kubernetes, add the code block rendered by printing this...

.. code-block:: bash

    cat <<EOF
            hosts {
                $AUTH_IP $AUTH_HOSTNAME
                $API_IP $API_HOSTNAME
                $UI_IP $UI_HOSTNAME
                fallthrough
            }
    EOF

...to the CoreDNS ConfigMap of your cluster, inside the ``.:53`` block right above the ``kubernetes`` plugin line...

.. code-block:: bash

    kubectl -n kube-system edit configmap $(kubectl -n kube-system get configmap -o name | grep coredns | head -n1 | cut -d/ -f2)

...and eventually make CoreDNS restart to let the ConfigMap change take effect:

.. code-block:: bash

    kubectl -n kube-system rollout restart deployments/coredns

For your host:

.. code-block:: bash

    echo "$AUTH_IP $AUTH_HOSTNAME" | sudo tee -a /etc/hosts
    echo "$API_IP $API_HOSTNAME" | sudo tee -a /etc/hosts
    echo "$UI_IP $UI_HOSTNAME" | sudo tee -a /etc/hosts

7. Wait for all of the charm applications in the ``istio-system``, ``iam`` and ``mlflow`` models to become ``active`` to know Charmed MLflow 3 is ready
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

.. code-block:: bash

    juju status --model istio-system --watch 5s
    juju status --model iam --watch 5s
    juju status --model mlflow --watch 5s


How to Access Charmed MLflow 3
------------------------------

Refer to :ref:`Configure RBAC Across MLflow Workspaces in Charmed MLflow 3 <configure_rbac_across_workspaces>` to configure user grants across MLflow workspaces and control programmatic and UI access to Charmed MLflow 3.
