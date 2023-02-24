#!/bin/bash

# This needs to be a separate file as newgrp will not have effect from  setup_environment.sh
microk8s enable rbac dns storage metallb:10.64.140.43-10.64.140.49
microk8s kubectl rollout status deployment/hostpath-provisioner -n kube-system

# Juju controller + model
juju bootstrap --agent-version=2.9.34 --no-gui microk8s uk8sx
juju add-model kubeflow

# Install + setup kubectl
sudo snap install kubectl --classic
mkdir -p /home/ubuntu/.kube
chown -f -R ubuntu /home/ubuntu/.kube
microk8s config > /home/ubuntu/.kube/config