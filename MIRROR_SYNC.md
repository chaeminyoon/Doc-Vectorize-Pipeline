# pgvector -> 일반 PostgreSQL 미러 테스트

## 목적

기존 파이프라인 컨테이너는 변경하지 않고, 별도 `odt-sync` 컨테이너가 서버 A의 pgvector DB를 읽어 서버 B의 일반 PostgreSQL로 복사한다. 서버 B의 `document_chunks.embedding`은 `vector`가 아니라 PostgreSQL 기본 `double precision[]`으로 저장된다. 따라서 서버 B에서는 pgvector 유사도 검색과 HNSW/IVFFlat 인덱스를 사용할 수 없다.

## 로컬 통합 테스트

이 저장소의 `docker-compose.mirror-test.yml`은 한 호스트에서 두 서버를 재현한다.

- `source-db`: `pgvector/pgvector:pg16` (서버 A 역할)
- `mirror-db`: `postgres:15` (서버 B 역할, pgvector 미설치)
- `sync`: 수동으로 실행하는 `odt-sync:latest`

```powershell
docker compose -f docker-compose.mirror-test.yml up -d source-db mirror-db
docker compose -f docker-compose.mirror-test.yml build sync
```

원본 DB에는 기존 파이프라인으로 최소 한 건 이상을 적재해야 한다. 로컬 테스트에서는 원본 DB에 연결할 때 다음 주소를 사용한다.

```text
postgresql://postgres:source-password@127.0.0.1:15432/vectordb
```

적재 후 sync를 실행한다.

```powershell
docker compose -f docker-compose.mirror-test.yml run --rm sync
```

서버 B 역할 DB에는 pgvector 확장이 없어야 한다.

```powershell
docker compose -f docker-compose.mirror-test.yml exec mirror-db `
  psql -U cmyoon -d vectordb -c "select extname from pg_extension where extname = 'vector';"
docker compose -f docker-compose.mirror-test.yml exec mirror-db `
  psql -U cmyoon -d vectordb -c "select count(*) from documents;"
docker compose -f docker-compose.mirror-test.yml exec mirror-db `
  psql -U cmyoon -d vectordb -c "select array_length(embedding, 1) from document_chunks where embedding is not null limit 1;"
```

첫 쿼리는 0행이어야 하며, 마지막 쿼리는 원본 임베딩 차원(현재 기본값 1024)을 반환해야 한다.

테스트 정리:

```powershell
docker compose -f docker-compose.mirror-test.yml down -v
```

## 실제 서버 배포

### 서버 B: 로컬 일반 PostgreSQL 준비

서버 B의 기존 로컬 PostgreSQL을 그대로 사용한다. 새로 설치해야 하는 경우에는 서버 B 운영체제의 공식 PostgreSQL 패키지만 설치하고 pgvector 패키지나 확장은 설치하지 않는다.

```bash
sudo -u postgres psql
```

```sql
CREATE ROLE cmyoon LOGIN PASSWORD '실제-비밀번호';
CREATE DATABASE vectordb OWNER cmyoon;
\q
```

이미 `cmyoon` 또는 `vectordb`가 있으면 해당 `CREATE` 문은 실행하지 않는다. 설치된 PostgreSQL 버전에 따라 설정 파일 위치가 다르므로, 실제 경로를 먼저 조회한다.

```bash
sudo -u postgres psql -tAc "show config_file;"
sudo -u postgres psql -tAc "show hba_file;"
```

조회된 `postgresql.conf`에는 다음을 설정한다.

```conf
listen_addresses = '*'
```

조회된 `pg_hba.conf`에는 서버 A만 허용하는 규칙을 추가한다. `scram-sha-256`은 서버 B의 기본 암호 방식이 SCRAM일 때 사용하며, 기존 시스템이 MD5라면 `md5`로 맞춘다.

```conf
host    vectordb    cmyoon    <SERVER_A_IP>/32    scram-sha-256
```

```bash
sudo systemctl restart postgresql
psql "postgresql://cmyoon:실제-비밀번호@127.0.0.1:5432/vectordb" -c "select version();"
psql "postgresql://cmyoon:실제-비밀번호@127.0.0.1:5432/vectordb" -c "select extname from pg_extension where extname = 'vector';"
```

두 번째 쿼리는 0행이어야 한다. 서버 B 방화벽에서도 서버 A(`<SERVER_A_IP>`)의 TCP 5432만 허용한다.

### 서버 A: sync 이미지 빌드 및 실행

서버 A에서 이 저장소를 가져온 뒤, 실제 자격 증명으로 `sync/sync.env`를 만든다. `odt-pipeline-db`와 `odt-pipeline-net`은 예시이므로 `docker ps`와 `docker network ls` 결과에 맞춰 바꾼다.

```bash
cp sync/sync.env.example sync/sync.env
chmod 600 sync/sync.env
docker build -f sync/Dockerfile -t odt-sync:latest .
```

기존 파이프라인 적재가 끝난 후에만 실행한다.

```bash
docker run --rm \
  --name odt-pipeline-sync \
  --network odt-pipeline-net \
  --env-file sync/sync.env \
  odt-sync:latest
```

초기 미러를 다시 만들 때만 대상 테이블을 삭제하고 재생성한다.

```bash
docker run --rm \
  --name odt-pipeline-sync \
  --network odt-pipeline-net \
  --env-file sync/sync.env \
  odt-sync:latest --drop-existing
```

`--drop-existing`은 서버 B의 `documents`, `document_chunks`, `attachments` 테이블만 대상으로 한다. 사업사 관리 테이블인 `st_partner_link_doc`는 삭제하지 않는다.
