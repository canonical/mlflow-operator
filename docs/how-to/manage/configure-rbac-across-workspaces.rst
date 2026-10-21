.. _configure_rbac_across_workspaces:

Configure RBAC Across MLflow Workspaces in Charmed MLflow 3 (Experimental)
==========================================================================

This guide applies to the newer, experimental Charmed MLflow 3 integrated with the Canonical Identity Platform, and describes how to configure user grants across MLflow workspaces and to control UI, external programmatic and in-cluster programmatic access to MLflow.

.. note::

    See :ref:`Deploy Charmed MLflow 3 with Terraform (Experimental) <deploy_mlflow_3_with_terraform>` for installing Charmed MLflow 3.

----------------------------------------------
Configuring Grants for Users Across Workspaces
----------------------------------------------

Different workspace-tier grants can be configured across different workspaces for different users.

For example, user A could have admin access to workspace X while having read-only access to workspace Y, while user B could have read-only access to workspace X, admin access to workspace Y and edit access to workspace Z.

To disambiguate, while the term user will be used loosely, the term "identity" will precisely refer to a user that exists in the charm ecosystem surrounding MLflow (e.g., Kratos users of the Canonical Identity Platform) and the term "MLflow user" will refer to users that only exist within the MLflow application.

By default, identities have no corresponding MLflow user and no grants across any MLflow workspaces.

For a given identity, all grants across any workspaces of interest can be:

1. defined by configuring one instance of a specifically configured data-integrator charm that represents the MLflow user for that identity, specifying:

   * ``entity-name`` as the identity, which will become the username of the corresponding MLflow user
   * ``entity-permissions`` as the workspace-tier grants for each workspace of interest, as described in :ref:`Syntax of entity-permissions <syntax_of_entity_permissions>`

2. applied by adding a Juju integration with that charm to Charmed MLflow over the ``mlflow_client`` interface

3. in case desirable, modified by updating the configurations of the same data-integrator charm instance:

   * if ``entity-permissions`` are modified, grants edits take effect live
   * if ``entity-name`` is modified, the Juju integration is to be reapplied (removed and re-added)

4. revoked by removing the Juju integration with the data-integrator charm

While MLflow users are just-in-time created when their first request reaches MLflow, MLflow user roles are instead automatically created as soon as grants are applied for the respective users and are automatically deleted when their grants are revoked to keep access locked. MLflow workspaces are also automatically created when grants are configured on them if they do not already exist, but they are instead never deleted when grants are removed to avoid unintended data loss.

----------------------------------
Mapping Identities to MLflow Users
----------------------------------

Three types of identities are supported by Charmed MLflow, based on the type of access being requested.

Each supported identity type is authenticated by and allowed to reach the MLflow application by the surrounding charm ecosystem (Istio & the Identity Platform), with MLflow treating any received identities as authenticated MLflow users (that are just-in-time created if newly seen), but without authorizing any operation until grants are explicitly defined for the respective users.

UI Access
~~~~~~~~~

For UI access, identities correspond to emails of users in the employed identity provider, which is Kratos for the Canonical Identity Platform.

The surrounding charm ecosystem will automatically handle the login via the identity provider and let the user reach MLflow maintaining the session and propagating the authenticated identity to MLflow.

To manage users in Kratos, follow this guide.

External Programmatic Access
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For programmatic access from outside the cluster, identities correspond to OAuth2 client IDs issued by the employed OpenID Connect provider, which is Hydra for the Canonical Identity Platform.

The surrounding charm ecosystem will expect access tokens generated for the respective OAuth2 clients to be passed as Bearer tokens in the Authorization header of requests sent by clients to MLflow. When not coding requests explicitly but using the MLflow Python client, setting `the ``MLFLOW_TRACKING_TOKEN`` environment variable <https://mlflow.org/docs/latest/api_reference/python_api/mlflow.environment_variables.html#mlflow.environment_variables.MLFLOW_TRACKING_TOKEN>`_ achieves the same header behind the scenes.

To create OAuth2 clients in Hydra, follow this guide.

To issue access tokens for OAuth2 clients from Hydra, follow this guide.

In-Cluster Programmatic Access
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

For programmatic access from within the cluster, identities correspond to names of namespaces traffic originates from. Only traffic from in-mesh namespaces is allowed.

The surrounding charm ecosystem will automatically impose the identity as the source namespace and clients will not be expected to set extra headers, unlike for external programmatic access.

-----------------------------------------------------
Aliasing Different Identities to the Same MLflow User
-----------------------------------------------------

In case different identities, be them of the same type or of different types, have to be mapped to the same MLflow user, Charmed MLflow can be configured to alias one identity to another via ``identity_aliases``.

For instance, one may want to be treated as the same MLflow user when reaching the UI after logging in with Kratos, when calling MLflow programmatically from outside the cluster with an access token issued for an OAuth2 client, and when connecting to MLflow from a Jupyter notebook run in a Kubeflow Profile (a workload from a namespace in the mesh). To do so:

.. code-block:: bash

    juju config --model mlflow mlflow-server identity_aliases="$OAUTH2_CLIENT_ID: $KRATOS_EMAIL, $K8S_NAMESPACE_NAME: $KRATOS_EMAIL"

.. _syntax_of_entity_permissions:

--------------------------------
Syntax of ``entity-permissions``
--------------------------------

The desired MLflow grants are configured via ``entity-permissions`` as a JSON list of objects each with ``resource_name``, ``resource_type`` and ``privileges``, with such object representings either workspace-wide grants or a cross-workspace super-admin.

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
