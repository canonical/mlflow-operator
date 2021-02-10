#!/usr/bin/env python3

import sys
from charmhelpers.core import hookenv
import logging

hooks = hookenv.Hooks()
logger = logging.getLogger(__name__)

@hooks.hook('mlflow-relation-joined', 'mlflow-relation-changed')
def mlflow_relation():
    logger.info("================================")
    logger.info("mlflow relation is running.")
    logger.info("================================")


@hooks.hook('mlflow-relation-departed', 'mlflow-relation-broken')
def mlflow_relation_gone():
    logger.info("================================")
    logger.info("mlflow relation is no longer present.")
    logger.info("================================")

if __name__ == '__main__':
    hooks.execute(sys.argv)
