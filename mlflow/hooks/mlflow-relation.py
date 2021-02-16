#!/usr/bin/env python3

import sys
from charmhelpers.core import hookenv
import logging

hooks = hookenv.Hooks()

@hooks.hook('mlflow-relation-joined', 'mlflow-relation-changed')
def mlflow_relation():
    hookenv.log("================================", "INFO")
    hookenv.log("mlflow relation is running.", "INFO")
    hookenv.log("================================", "INFO")


@hooks.hook('mlflow-relation-departed', 'mlflow-relation-broken')
def mlflow_relation_gone():
    hookenv.log("================================", "INFO")
    hookenv.log("mlflow relation is no longer present.", "INFO")
    hookenv.log("================================", "INFO")

if __name__ == '__main__':
    hooks.execute(sys.argv)
