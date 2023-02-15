# Training Operator

## Overview

This repository hosts the Kubernetes Training Operator for Kubeflow training jobs.

## Description

The [Kubeflow Training Operator][1] provides Kubernetes custom resources to run distributed or
non-distributed training jobs, such as TFJobs and PytorchJobs. The Training Operator in this
repository is a Python script which wraps the latest released Kubeflow Training Operator
[manifests][2], providing lifecycle management and handling events (install, upgrade, integrate,
remove). It is one of the [Charmed Kubeflow][3] operators.

## Usage

While it is possible to deploy the Training Operator as a standalone operator, it works best when
deployed alongside other components included in the Kubeflow bundle. For installation steps, please
refer to the [installation][4] guide.

[1]: https://www.kubeflow.org/docs/components/training/
[2]: https://github.com/kubeflow/manifests/tree/master/apps/training-operator
[3]: https://github.com/canonical/bundle-kubeflow
[4]: https://charmed-kubeflow.io/docs/install
