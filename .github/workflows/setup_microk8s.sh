#!/bin/bash
microk8s enable rbac dns storage metallb:10.64.140.43-10.64.140.49
microk8s kubectl rollout status deployment/hostpath-provisioner -n kube-system
juju bootstrap --agent-version=2.9.34 --no-gui microk8s uk8sx
juju add-model kubeflow
sudo snap install kubectl --classic
mkdir -p /home/ubuntu/.kube
chown -f -R ubuntu /home/ubuntu/.kube
microk8s config > /home/ubuntu/.kube/config