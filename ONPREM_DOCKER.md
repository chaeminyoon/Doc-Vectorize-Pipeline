# On-Prem Docker Deployment

This project can run in a fully air-gapped Ubuntu 18.04 environment with Docker only.

## Assumptions

- Internet access is unavailable
- `pip` cannot be used
- The server has only the base Python installed
- Docker is installed
- The deployment is done from a prebuilt bundle created on another machine

The server does not need package installation from source. The app runs from the prebuilt container image.

## What To Transfer

Transfer the whole deployment folder generated from the bundle.

Required contents:

- `docker-images.tar`
- `.env.onprem.example`
- `HOW_TO_RUN.md`
- `ONPREM_DOCKER.md`
- `data/`
- `metadata/`
- `models/`

The outer directory name can change. The internal structure must stay unchanged.

## Load Images

```bash
cd /home/deploy/odt-pipeline-deploy
docker load -i docker-images.tar
```

## Prepare Runtime Configuration

```bash
cp .env.onprem.example .env.onprem
```

Edit `.env.onprem`:

```env
DATABASE_URL=postgresql://postgres:change-me@odt-pipeline-db:5432/vectordb
```

The PostgreSQL password here must match the database container startup password.

Optional Server B sync target:

```env
MIRROR_DATABASE_URL=postgresql://postgres:change-me@SERVER_B_IP:5432/vectordb
```

Leave `MIRROR_DATABASE_URL` empty to disable sync. The Server B database
does not need pgvector. The app creates plain PostgreSQL tables and stores
`document_chunks.embedding` as `double precision[]`.

## Start the Database Without Docker Compose

Create shared Docker resources once:

```bash
docker network create odt-pipeline-net
docker volume create odt-pipeline-pgdata
```

Start PostgreSQL:

```bash
docker rm -f odt-pipeline-db 2>/dev/null || true

docker run -d \
  --name odt-pipeline-db \
  --restart unless-stopped \
  --network odt-pipeline-net \
  -e POSTGRES_DB=vectordb \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=change-me \
  -p 5432:5432 \
  -v odt-pipeline-pgdata:/var/lib/postgresql/data \
  pgvector/pgvector:pg16
```

Wait for readiness:

```bash
until docker exec odt-pipeline-db pg_isready -U postgres -d vectordb; do
  sleep 5
done
```

## Run the App Without Docker Compose

Initial full load:

```bash
docker run --rm \
  --name odt-pipeline-app \
  --network odt-pipeline-net \
  --env-file .env.onprem \
  -v "$PWD/data:/app/data" \
  -v "$PWD/metadata:/app/metadata:ro" \
  -v "$PWD/models:/models:ro" \
  odt-pipeline:onprem run --init-db
```

Incremental load:

```bash
docker run --rm \
  --name odt-pipeline-app \
  --network odt-pipeline-net \
  --env-file .env.onprem \
  -v "$PWD/data:/app/data" \
  -v "$PWD/metadata:/app/metadata:ro" \
  -v "$PWD/models:/models:ro" \
  odt-pipeline:onprem run
```

Sync Server A DB into Server B:

```bash
docker run --rm \
  --network odt-pipeline-net \
  --env-file .env.onprem \
  odt-pipeline:onprem sync-to-mirror
```

Stats:

```bash
docker run --rm \
  --network odt-pipeline-net \
  --env-file .env.onprem \
  -v "$PWD/data:/app/data" \
  -v "$PWD/metadata:/app/metadata:ro" \
  -v "$PWD/models:/models:ro" \
  odt-pipeline:onprem stats
```

Search:

```bash
docker run --rm \
  --network odt-pipeline-net \
  --env-file .env.onprem \
  -v "$PWD/data:/app/data" \
  -v "$PWD/metadata:/app/metadata:ro" \
  -v "$PWD/models:/models:ro" \
  odt-pipeline:onprem search "공유수면 매립"
```

## Default Runtime Expectations

- metadata directory inside container: `/app/metadata`
- data root inside container: `/app/data`
- processing target: immediate year-like child directories under `/app/data` are discovered automatically
- model directory inside container: `/models/bge-m3`
- embedded HWP image extraction: enabled by default

## Automation With Cron

Use host cron, not container cron.

Example script:

```bash
#!/bin/bash
set -euo pipefail

cd /home/deploy/odt-pipeline-deploy

/usr/bin/docker run --rm \
  --name odt-pipeline-app \
  --network odt-pipeline-net \
  --env-file .env.onprem \
  -v /home/deploy/odt-pipeline-deploy/data:/app/data \
  -v /home/deploy/odt-pipeline-deploy/metadata:/app/metadata:ro \
  -v /home/deploy/odt-pipeline-deploy/models:/models:ro \
  odt-pipeline:onprem run
```

Example crontab:

```cron
*/10 * * * * /usr/bin/flock -n /tmp/odt-pipeline.lock /home/deploy/odt-pipeline-deploy/run_incremental.sh >> /home/deploy/odt-pipeline-deploy/cron.log 2>&1
```

## Notes

- Use absolute paths in cron and shell scripts.
- Do not run `pip install -r requirements.txt` on the air-gapped server.
- If upstream systems place files into `data/` continuously, they should expose files only after copy completion.
- If HWPX conversion is needed, rebuild the bundle with LibreOffice support before transfer.
