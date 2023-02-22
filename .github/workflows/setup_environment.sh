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
newgrp lxd
sudo apt-get update -yqq
sudo apt-get install -yqq python3-pip
sudo --preserve-env=http_proxy,https_proxy,no_proxy pip3 install tox
sudo snap install juju --classic --channel=latest/stable
sudo snap install jq
sudo snap install charm --classic --channel=latest/stable
sudo snap install charmcraft --classic --channel=latest/stable
sudo snap install juju-bundle --classic --channel=latest/stable
sudo snap install juju-crashdump --classic --channel=latest/stable
sudo snap install microk8s --channel=1.22/stable --classic
sudo snap refresh charmcraft --channel latest/candidate
sudo usermod -a -G microk8s ubuntu
sudo chown -f -R ubuntu /home/ubuntu/.kube
newgrp microk8s
