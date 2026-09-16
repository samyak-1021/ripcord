# Infrastructure as code for a self-hosted Ripcord deployment.
#
# Uses the Docker provider so the whole stack — Postgres, Redis, and the API
# image (built from the repo Dockerfile) — is declared and reproducible:
#
#   cd terraform && terraform init && terraform apply
#
# (Stop the docker-compose stack first; both bind host port 8000.)

terraform {
  required_version = ">= 1.6"
  required_providers {
    docker = {
      source  = "kreuzwerker/docker"
      version = "~> 3.0"
    }
  }
}

provider "docker" {}

variable "postgres_password" {
  type        = string
  default     = "ripcord"
  description = "Password for the Postgres role."
}

variable "api_port" {
  type        = number
  default     = 8000
  description = "Host port to expose the API on."
}

variable "bootstrap_admin_key" {
  type        = string
  sensitive   = true
  description = <<-EOT
    Admin key accepted without a database lookup, used to mint the first real
    key. Must look like rpc_<12 hex chars>_<secret>; the API refuses to start
    otherwise. Generate one with `python -m ripcord.cli mint-bootstrap`.
  EOT

  validation {
    condition     = can(regex("^rpc_[0-9a-f]{12}_.+$", var.bootstrap_admin_key))
    error_message = "Must be rpc_<12 hex chars>_<secret>."
  }
}

resource "docker_network" "ripcord" {
  name = "ripcord-net"
}

resource "docker_image" "postgres" {
  name = "postgres:16"
}

resource "docker_image" "redis" {
  name = "redis:7"
}

resource "docker_image" "api" {
  name = "ripcord:latest"
  build {
    context = ".." # repo root, where the Dockerfile lives
  }
}

resource "docker_container" "postgres" {
  name  = "ripcord-tf-postgres"
  image = docker_image.postgres.image_id
  env = [
    "POSTGRES_USER=ripcord",
    "POSTGRES_PASSWORD=${var.postgres_password}",
    "POSTGRES_DB=ripcord",
  ]
  networks_advanced {
    name = docker_network.ripcord.name
  }
}

resource "docker_container" "redis" {
  name  = "ripcord-tf-redis"
  image = docker_image.redis.image_id
  networks_advanced {
    name = docker_network.ripcord.name
  }
}

resource "docker_container" "api" {
  name  = "ripcord-tf-api"
  image = docker_image.api.image_id
  env = [
    "DATABASE_URL=postgresql+asyncpg://ripcord:${var.postgres_password}@ripcord-tf-postgres:5432/ripcord",
    "REDIS_URL=redis://ripcord-tf-redis:6379/0",
    # Without this the stack comes up with auth on and an empty api_keys table,
    # so there is no way to mint the first key over HTTP and every route but
    # /health answers 401. A bootstrap credential is not optional for a stack
    # that is supposed to be usable the moment `terraform apply` finishes.
    "BOOTSTRAP_ADMIN_KEY=${var.bootstrap_admin_key}",
  ]
  ports {
    internal = 8000
    external = var.api_port
  }
  networks_advanced {
    name = docker_network.ripcord.name
  }
  depends_on = [docker_container.postgres, docker_container.redis]
}
