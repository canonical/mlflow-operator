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
    relational_db = "relational-db"
    object_storage = "object-storage"
    dashboard_links = "dashboard-links"
    ingress = "ingress"
    secrets = "secrets"
    pod_defaults = "pod-defaults"
  }
}
