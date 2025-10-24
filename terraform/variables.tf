variable "app_name" {
  description = "Application name"
  type        = string
  default     = "mlflow-server"
}

variable "base" {
  description = "Charm base"
  type        = string
  default     = "ubuntu@24.04"
}

variable "channel" {
  description = "Charm channel"
  type        = string
  default     = "latest/edge"
}

variable "config" {
  description = "Map of charm configuration options"
  type        = map(string)
  default     = {}
}

variable "model_name" {
  description = "Model name"
  type        = string
}

variable "resources" {
  description = "Map of resources"
  type        = map(string)
  default     = null
}

variable "revision" {
  description = "Charm revision"
  type        = number
  default     = null
}
