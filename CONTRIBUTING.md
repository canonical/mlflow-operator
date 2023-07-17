# Contributing

## Overview

This document explains the processes and practices recommended for contributing enhancements to
this operator.

## Talk to us First

Before developing enhancements to this charm, you should [open an issue](/../../issues) explaining your use case. If you would like to chat with us about your use-cases or proposed implementation, you can reach us at [MLOps Mattermost public channel](https://chat.charmhub.io/charmhub/channels/mlops-documentation) or on [Discourse](https://discourse.charmhub.io/).

## Pull Requests

Please help us out in ensuring easy to review branches by rebasing your pull request branch onto the `main` branch. This also avoids merge commits and creates a linear Git commit history.

All pull requests require review before being merged. Code review typically examines:
  - code quality
  - test coverage
  - user experience for Juju administrators of this charm.

## Recommended Knowledge

Familiarising yourself with the [Charmed Operator Framework](https://juju.is/docs/sdk) library will help you a lot when working on new features or bug fixes.

## Developing

You can use the environments created by `tox` for development:

```shell
tox --notest -e unit
source .tox/unit/bin/activate
```

### Testing

```shell
tox -e lint          # code style
tox -e unit          # unit tests
tox -e integration   # integration tests
tox                  # runs 'lint' and 'unit' environments
```

## Build Charm

Build the charm in this git repository using:

```shell
charmcraft pack
```

### Deploy

```bash
# Create a model
juju add-model dev
# Enable DEBUG logging
juju model-config logging-config="<root>=INFO;unit=DEBUG"
# Deploy the charm
juju deploy ./mlflow-server_ubuntu-20.04-amd64.charm \
    --resource oci-image=$(yq '.resources."oci-image"."upstream-source"' metadata.yaml)
```

## Updating the charm for new versions of the workload

To upgrade the source and resources of this charm, you must:

1. Bump the `oci-image` in `metadata.yaml`
1. Update the charm source for any changes, such as:
    - YAML manifests in `src/` and/or any Kubernetes resource in `pod_spec`
    - New or changed configurations passed to pebble workloads or through `pod.set_spec`
1. Ensure integration and unit tests are passing; fix/adapt them otherwise

## Canonical Contributor Agreement

Canonical welcomes contributions to this charm. Please check out our [contributor agreement](https://ubuntu.com/legal/contributors) if you're interested in contributing.
