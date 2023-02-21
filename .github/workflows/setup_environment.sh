#!/bin/bash
whoami
echo $USER
sudo snap install core
sudo apt-get remove -qy lxd lxd-client
sudo snap install lxd --channel=latest/stable
sudo snap refresh lxd --channel=latest/stable
lxd waitready
lxd init --auto
chmod a+wr /var/snap/lxd/common/lxd/unix.socket
lxc network set lxdbr0 ipv6.address none
sudo usermod -a -G lxd ubuntu
sudo chown -f -R ubuntu /home/ubuntu/.kube
su ubuntu
sudo apt-get update -yqq
sudo apt-get install -yqq python3-pip
sudo --preserve-env=http_proxy,https_proxy,no_proxy pip3 install tox
sudo snap install juju --classic --channel=latest/stable
sudo snap install jq
sudo snap install charm --classic --channel=latest/stable
sudo snap install charmcraft --classic --channel=latest/stable
sudo snap install juju-bundle --classic --channel=latest/stable
sudo snap install juju-crashdump --classic --channel=latest/stable
sudo snap install microk8s --classic --channel=1.22/stable
sudo snap refresh charmcraft --channel latest/candidate
sudo usermod -a -G microk8s ubuntu
sudo chown -f -R ubuntu /home/ubuntu/.kube
microk8s enable dns storage rbac metallb:10.64.140.43-10.64.140.49
microk8s kubectl -n kube-system rollout status deployment/hostpath-provisioner
juju bootstrap --debug --verbose microk8s uk8s-controller --model-default test-mode=true --model-default automatically-retry-hooks=false --model-default logging-config="<root>=DEBUG" --agent-version=2.9.34 --bootstrap-constraints=""
juju add-model testing
sudo snap install kubectl --classic
mkdir -p /home/ubuntu/.kube
chown -f -R ubuntu /home/ubuntu/.kube