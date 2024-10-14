output "app_name" {
  value = juju_application.mlflow_server.name
}

output "provides" {
  value = {
    grafana_dashboard = "grafana-dashboard",
    metrics_endpoint  = "metrics-endpoint",
  }
}

output "requires" {
  value = {
    dashboard_links = "dashboard-links",
    ingress         = "ingress",
    object_storage  = "object-storage",
    pod_defaults    = "pod-defaults",
    relational_db   = "relational-db",
    secrets         = "secrets",
    logging         = "logging",
  }
}
