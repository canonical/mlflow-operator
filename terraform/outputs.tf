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
    dashboard_links     = "dashboard-links",
    ingress             = "ingress",
    istio_ingress_route = "istio-ingress-route",
    object_storage      = "object-storage",
    pod_defaults        = "pod-defaults",
    backend_store_db    = "backend-store-db",
    auth_db             = "auth-db",
    require_cmr_mesh    = "require-cmr-mesh",
    s3_credentials      = "s3-credentials",
    secrets             = "secrets",
    service_mesh        = "service-mesh",
    logging             = "logging",
  }
}
