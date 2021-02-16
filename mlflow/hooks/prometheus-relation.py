#!/usr/bin/env python3

import sys
from charmhelpers.core import hookenv
hooks = hookenv.Hooks()

@hooks.hook('prometheus-relation-joined', 'prometheus-relation-changed', 'prometheus-relation-created')
def prometheus_relation():
    hookenv.log("================================", "INFO")
    hookenv.log("prometheus relation is running.", "INFO")
    hookenv.log("================================", "INFO")
    
    config = hookenv.config()

    relation_data = { "port": config['mlflow_port'],
                     "metrics_path": "/metrics"}
    if config['mlflow_scrape_interval']:
      relation_data["scrape_interval"] = config['mlflow_scrape_interval']
    if config['mlflow_scrape_timeout']:
      relation_data["scrape_timeout"] = config['mlflow_scrape_timeout']

    # Set the relation data on the relation.
    hookenv.relation_set(hookenv.relation_id(), relation_settings=relation_data )

@hooks.hook('prometheus-relation-departed', 'prometheus-relation-broken')
def prometheus_relation_gone():
    hookenv.log("================================", "INFO")
    hookenv.log("prometheus relation is no longer present.", "INFO")
    hookenv.log("================================", "INFO")

if __name__ == '__main__':
    hooks.execute(sys.argv)
