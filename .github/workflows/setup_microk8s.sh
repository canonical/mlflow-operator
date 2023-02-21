#!/bin/bash
microk8s enable dns storage rbac metallb:10.64.140.43-10.64.140.49
microk8s kubectl -n kube-system rollout status deployment/hostpath-provisioner
juju bootstrap --debug --verbose microk8s uk8s-controller --model-default test-mode=true --model-default automatically-retry-hooks=false --model-default logging-config="<root>=DEBUG" --agent-version=2.9.34 --bootstrap-constraints=""
juju add-model testing
sudo snap install kubectl --classic
mkdir -p /home/ubuntu/.kube
chown -f -R ubuntu /home/ubuntu/.kube