# Terraform

Brings up the same three containers `docker-compose.yml` does — Postgres, Redis
and the API — through the Docker provider, so the stack is described as code
rather than as a list of commands in a README.

It demonstrates the shape; it is not a production module. The Docker provider
runs containers on whatever machine holds the socket. A real deployment would
swap the provider for ECS, Cloud Run or a Helm release; what carries over is the
variable surface and the dependency ordering.

## Using it

```bash
cd terraform
terraform init

# The API refuses to start without a well-formed bootstrap key, so generate one
# rather than inventing a string: a non-hex key_id parses as garbage and
# authenticates nobody.
terraform apply \
  -var "postgres_password=$(openssl rand -hex 16)" \
  -var "bootstrap_admin_key=$(python -m ripcord.cli mint-bootstrap)"
```

Then mint a real key and stop using the bootstrap one:

```bash
curl -X POST localhost:8000/keys \
  -H "Authorization: Bearer $BOOTSTRAP_ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"name": "dashboard", "scopes": ["flags:read", "flags:write"]}'
```

## Known limitation

The API container runs `alembic upgrade head` on start, and the Docker provider
has no equivalent of compose's `depends_on: condition: service_healthy`, so on a
cold `apply` the migration can race Postgres accepting connections. The
container restarts and succeeds on the next attempt; a production module would
use a readiness gate rather than rely on that.

## Teardown

```bash
terraform destroy -var "postgres_password=..." -var "bootstrap_admin_key=..."
```
