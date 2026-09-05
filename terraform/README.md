# Terraform

Declarative infra for a self-hosted Ripcord stack, via the
[Docker provider](https://registry.terraform.io/providers/kreuzwerker/docker/latest).
It builds the API image from the repo `Dockerfile` and runs it alongside
Postgres and Redis on a private Docker network.

```bash
cd terraform
terraform init      # download the docker provider
terraform validate  # check the config
terraform apply     # build + run the stack (API on :8000)
terraform destroy   # tear it all down
```

> Stop the `docker compose` stack first — both bind host port 8000.

This is the "reproducible infra" counterpart to `docker-compose.yml` (which is
the quick dev loop). Same containers, declared as code with explicit
dependencies and variables.
