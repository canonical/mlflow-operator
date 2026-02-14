output "app_name" {
  value = juju_application.mlflow_server.name
}

output "provides" {
  value = {
    grafana_dashboard = "grafana-dashboard",
    metrics_endpoint  = "metrics-endpoint",
    provide_cmr_mesh  = "provide-cmr-mesh",
  }
}

output "requires" {
  value = {
    dashboard_links  = "dashboard-links",
    ingress          = "ingress",
    object_storage   = "object-storage",
    pod_defaults     = "pod-defaults",
    relational_db    = "relational-db",
    require_cmr_mesh = "require-cmr-mesh",
    secrets          = "secrets",
    service_mesh     = "service-mesh",
    logging          = "logging",
  }
}
