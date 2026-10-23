.. _configure_rbac_across_workspaces:

Configure RBAC Across MLflow Workspaces in Charmed MLflow 3 (Experimental)
==========================================================================

This guide applies to the newer, experimental Charmed MLflow 3 integrated with the Canonical Identity Platform, and describes how to configure user grants across MLflow workspaces and to control UI, external programmatic and in-cluster programmatic access to MLflow.

.. note::

    See :ref:`Deploy Charmed MLflow 3 with Terraform (Experimental) <deploy_mlflow_3_with_terraform>` for installing Charmed MLflow 3.

.. note::

    In this guide, the term "user" will be used loosely. To disambiguate:

    * the term "identity" will precisely refer to a user that exists in the charm ecosystem surrounding MLflow (e.g., Kratos users of the Canonical Identity Platform)

    * the term "MLflow user" will refer to users that only exist within the MLflow application

.. _configuring_grants_for_users_across_workspaces:

----------------------------------------------
Configuring Grants for Users Across Workspaces
----------------------------------------------

Different workspace-tier grants can be configured across different MLflow workspaces for different identities that exists in the charm ecosystem surrounding MLflow. See :ref:`mapping_identities_to_mlflow_users` to understand what such identities are.

For a given identity, the desired grants across any workspaces of interest are:

1. defined by deploying and configuring a ``data-integrator`` charm instance for that identity, specifying:

   * ``entity-name`` as the identity, which will become the corresponding MLflow user's username
   * ``entity-permissions`` as the workspace-tier grants for each workspace of interest, as described in :ref:`Syntax of entity-permissions <syntax_of_entity_permissions>`

   For example, to grant the identity `my-identity` admin access to the `analytics-team` workspace and read-only access to the `data-team` workspace:

   .. code-block:: bash

       juju deploy data-integrator mlflow-grants-for-my-identity --trust --model mlflow --channel latest/edge \
           --config entity-name=my-identity \
           --config entity-permissions='[{"resource_type": "workspace", "resource_name": "analytics-team", "privileges": ["admin"]}, {"resource_type": "workspace", "resource_name": "data-team", "privileges": ["read-only"]}]'

2. applied by integrating the same charm instance to Charmed MLflow over the ``mlflow_client`` interface

   For instance, continuing the example above:

   .. code-block:: bash

       juju integrate mlflow-server:mlflow-client mlflow-grants-for-my-identity:mlflow

3. in case desirable, modified by updating the configurations of the charm instance:

   * if ``entity-permissions`` are modified, grant edits take effect live
   * if ``entity-name`` is modified, the Juju integration is to be reapplied (removed and re-added)

4. revoked by removing the Juju integration

   For instance, continuing the example above:

   .. code-block:: bash

       juju remove-relation mlflow-server:mlflow-client mlflow-grants-for-my-identity:mlflow

By default, identities have no grants across any MLflow workspaces. 

.. note::

    While MLflow users are just-in-time created when their first request reaches MLflow, MLflow user roles are instead automatically created as soon as grants are applied for the respective users and are automatically deleted when their grants are revoked to keep access locked. MLflow workspaces are also automatically created when grants are configured on them if they do not already exist, but they are instead never deleted when grants are removed to avoid unintended data loss.

.. _mapping_identities_to_mlflow_users:

----------------------------------
Mapping Identities to MLflow Users
----------------------------------

The following three types of identities are supported by Charmed MLflow.

.. note::

    Each supported identity type is authenticated by and allowed to reach the MLflow application by the surrounding charm ecosystem (Istio & the Identity Platform). MLflow treats any received identities as authenticated MLflow users (that are just-in-time created if newly seen), but without authorizing any operation until grants are explicitly defined as described in :ref:`Configuring Grants for Users Across Workspaces <configuring_grants_for_users_across_workspaces>`.

UI Access
~~~~~~~~~

For UI access, identities correspond to emails of users in the employed identity provider, which is Kratos for the Canonical Identity Platform.

To create and manage Kratos users in the Canonical Identity Platform, follow `Manage users <https://canonical-identity.readthedocs-hosted.com/identity-platform/how-to/manage-users/>`_.

The charm ecosystem will handle the login via the identity provider and let the user reach MLflow.

External Programmatic Access
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For programmatic access from outside the cluster, identities correspond to OAuth2 client IDs issued by the employed OpenID Connect provider, which is Hydra for the Canonical Identity Platform.

To create OAuth2 clients in Hydra with the Canonical Identity Platform, follow `Onboard an application with Charmed Hydra <https://canonical-identity.readthedocs-hosted.com/identity-platform/how-to/onboard-an-application-with-charmed-hydra/#register-your-oauth-client>`_. For example:

.. code-block:: bash

    juju run -m iam hydra/0 create-oauth-client \
        name="client-for-your-m2m-app" \
        grant-types='["client_credentials"]' \
        response-types='["token"]' \
        scope='["openid"]'

The charm ecosystem will expect access tokens generated for the respective OAuth2 clients to be passed as Bearer tokens in requests sent by clients to MLflow (either directly as headers or via `the MLFLOW_TRACKING_TOKEN environment variable <https://mlflow.org/docs/latest/api_reference/python_api/mlflow.environment_variables.html#mlflow.environment_variables.MLFLOW_TRACKING_TOKEN>`_ when using the MLflow Python client).

To issue such access tokens, make a POST request to the ``/oauth2/token`` endpoint path at your auth hostname using the OAuth2 client ID and secret returned by the previous command as Basic Auth credentials and passing ``grant_type=client_credentials`` and ``scope=openid`` in the request body. For example:

.. code-block:: bash

    ACCESS_TOKEN=$(curl -sk -X POST https://auth.mlflow.com/oauth2/token \
        -u "$OAUTH2_CLIENT_ID:$OAUTH2_CLIENT_SECRET" \
        -d "grant_type=client_credentials" \
        -d "scope=openid" \
        | jq -r '.access_token')

.. note::

    The token lifetime cannot currently be configured via the charm and it defaults to 1 hour.

In-Cluster Programmatic Access
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For programmatic access from within the cluster, identities correspond to names of namespaces traffic originates from. Only traffic from in-mesh namespaces is allowed.

The charm ecosystem will automatically impose the identity and clients will not be expected to set Bearer tokens, unlike for external programmatic access.

-----------------------------------------------------
Aliasing Different Identities to the Same MLflow User
-----------------------------------------------------

In case different identities, be them of the same type or of different types, have to be mapped to the same MLflow user, Charmed MLflow can be configured to alias one identity to another via ``identity_aliases``.

For instance, one may want to be treated as the same MLflow user when reaching the UI after logging in with Kratos, when calling MLflow programmatically from outside the cluster with an access token issued for a given OAuth2 client, and when connecting to MLflow from an in-cluster workload from a namespace in the mesh. To do so:

.. code-block:: bash

    juju config --model mlflow mlflow-server identity_aliases="$OAUTH2_CLIENT_ID: $KRATOS_EMAIL, $K8S_NAMESPACE_NAME: $KRATOS_EMAIL"

In this case, only the identity ``$KRATOS_EMAIL`` is to be granted workspace access via ``data-integrator``, being the only identity directly mapped to an MLflow user, while ``$OAUTH2_CLIENT_ID`` and ``$K8S_NAMESPACE_NAME`` need no direct grants via ``data-integrator`` as they alias that same MLflow user.

.. _syntax_of_entity_permissions:

--------------------------------
Syntax of ``entity-permissions``
--------------------------------

The desired MLflow grants are configured via ``entity-permissions`` as a JSON list of objects each with ``resource_name``, ``resource_type`` and ``privileges``, with such object representing either workspace-wide grants or a cross-workspace super-admin.

Workspace-Wide Grants
~~~~~~~~~~~~~~~~~~~~~

Each grant is defined with ``resource_type`` set to ``workspace``, with ``resource_name`` as the workspace name and with ``privileges`` containing one of the allowed permission levels, which provide sensible combinations of `the upstream permission levels <https://mlflow.org/docs/latest/self-hosting/security/role-based-access-control/#permission-levels>`_ and `workspace permissions <https://mlflow.org/docs/latest/self-hosting/workspaces/permissions/>`_ to cover the most common use cases:
- ``read-only``: native ``READ`` type-wide grants on all resource types in the workspace
- ``member``:  native ``USE`` workspace-tier grant on the workspace
- ``edit``  native ``USE`` workspace-tier grant on the workspace plus native ``EDIT`` type-wide grants on all resource types in the workspace
- ``admin``:  native ``MANAGE`` workspace-tier grant on the workspace

For instance:

.. code-block:: bash

    juju config data-integrator entity-name='my-username' entity-permissions='[{"resource_type": "workspace", "resource_name": "analytics-team", "privileges": ["admin"]}, {"resource_type": "workspace", "resource_name": "data-team", "privileges": ["read-only"]}]'

A Cross-Workspace Super-Admin Grant
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

The (only) grant is defined with ``resource_type`` set to ``super-admin`` and without any redundant ``resource_name`` or ``privileges``, since super-admins can already access all workspaces with admin privileges. This corresponds to `the upstream platform admin <https://mlflow.org/docs/latest/self-hosting/security/role-based-access-control#user-tiers>`_.

For instance:

.. code-block:: bash

    juju config data-integrator entity-name='my-username' entity-permissions='[{"resource_type": "super-admin", "resource_name": "", "privileges": []}]'
