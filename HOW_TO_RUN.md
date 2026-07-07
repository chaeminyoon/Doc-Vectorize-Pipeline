# How To Run

## Recommended Target

This deployment guide assumes:

- Ubuntu 18.04
- No internet access
- `pip` unavailable
- Python packages cannot be installed on the server
- Docker is installed

The server does not need a Python virtual environment. Run everything through Docker images included in the on-prem bundle.

## What To Bring Into the Air-Gapped Server

Bring the entire deployment folder created from the bundle, not individual files.

Minimum required contents:

- `docker-images.tar`
- `.env.onprem.example`
- `data/`
- `metadata/`
- `models/`
- `HOW_TO_RUN.md`
- `ONPREM_DOCKER.md`

The outer folder name can be changed. The inner structure must stay the same.

Example:

```text
odt-pipeline-deploy/
├─ docker-images.tar
├─ .env.onprem.example
├─ HOW_TO_RUN.md
├─ ONPREM_DOCKER.md
├─ data/
├─ metadata/
└─ models/
```

## Server-Side Deployment

The commands below use only `docker`. They do not require `pip`, `python main.py`, or `docker compose`.

### 1. Move Into the Deployment Folder

Use the real absolute path on the server. Do not rely on `~` inside cron scripts.

```bash
cd /home/deploy/odt-pipeline-deploy
```

### 2. Load the Images

```bash
docker load -i docker-images.tar
```

This loads:

- `odt-pipeline:onprem`
- `pgvector/pgvector:pg16`

### 3. Create the Runtime Env File

```bash
cp .env.onprem.example .env.onprem
```

Edit `.env.onprem` and change at least:

```env
DATABASE_URL=postgresql://postgres:change-me@odt-pipeline-db:5432/vectordb
```

Use the real PostgreSQL password instead of `change-me`.

To sync already-saved Server A database results into a separate PostgreSQL
server that does not have pgvector installed, set `MIRROR_DATABASE_URL`:

```env
MIRROR_DATABASE_URL=postgresql://postgres:change-me@SERVER_B_IP:5432/vectordb
```

Leave `MIRROR_DATABASE_URL` empty to disable sync. The mirror database uses
plain PostgreSQL tables; `document_chunks.embedding` is stored as
`double precision[]`, not the pgvector `vector` type.

### 4. Verify the Mounted Input Layout

The app container expects:

- `./data` mounted to `/app/data`
- `./metadata` mounted to `/app/metadata`
- `./models` mounted to `/models`

At runtime:

- metadata path: `/app/metadata`
- data root: `/app/data`
- processing target: immediate year-like child directories under `/app/data` are discovered automatically
- embedded HWP image extraction: enabled by default

If you receive new files from another system, update the contents of `data/` and `metadata/` in this deployment folder before running the app.

### 5. Create Docker Network and Volume

These are created once.

```bash
docker network create odt-pipeline-net
docker volume create odt-pipeline-pgdata
```

If they already exist, Docker will report that and keep going.

### 6. Start PostgreSQL

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

If you changed the DB password in `.env.onprem`, set the same value in `POSTGRES_PASSWORD` here.

### 7. Wait Until DB Is Ready

```bash
until docker exec odt-pipeline-db pg_isready -U postgres -d vectordb; do
  sleep 5
done
```

### 8. Run the Initial Full Ingest

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

### 9. Run Incremental Ingest Later

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

### 10. Sync Server A DB Into Server B

Run this after `run` finishes when you want to copy Server A DB contents into
Server B.

```bash
docker run --rm \
  --network odt-pipeline-net \
  --env-file .env.onprem \
  odt-pipeline:onprem sync-to-mirror
```

## Useful Commands

### Show Stats

```bash
docker run --rm \
  --network odt-pipeline-net \
  --env-file .env.onprem \
  -v "$PWD/data:/app/data" \
  -v "$PWD/metadata:/app/metadata:ro" \
  -v "$PWD/models:/models:ro" \
  odt-pipeline:onprem stats
```

### Run Search

```bash
docker run --rm \
  --network odt-pipeline-net \
  --env-file .env.onprem \
  -v "$PWD/data:/app/data" \
  -v "$PWD/metadata:/app/metadata:ro" \
  -v "$PWD/models:/models:ro" \
  odt-pipeline:onprem search "공유수면 매립"
```

## Cron Example

If new files keep arriving under `data/` and `metadata/`, use host cron. Do not put cron inside the container.

Create `/home/deploy/odt-pipeline-deploy/run_incremental.sh`:

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

Make it executable:

```bash
chmod +x /home/deploy/odt-pipeline-deploy/run_incremental.sh
```

Register cron with a lock file so jobs do not overlap:

```bash
crontab -e
```

```cron
*/10 * * * * /usr/bin/flock -n /tmp/odt-pipeline.lock /home/deploy/odt-pipeline-deploy/run_incremental.sh >> /home/deploy/odt-pipeline-deploy/cron.log 2>&1
```

## Important Notes

- Use absolute paths in shell scripts and cron.
- If upstream systems copy files into `data/` slowly, they should write to a temp location first and move them into the final folder only after the copy completes.
- If HWPX to PDF conversion is required, the bundle must be built with LibreOffice included. Otherwise keep `CONVERT_HWPX=false`.
- The server does not need `pip install -r requirements.txt`.
- The server does not need to run `python main.py ...` directly.
