# Document-to-Vector Search Pipeline for Korean Government Records (ODT → pgvector)

**English** | [한국어](README.ko.md)

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)
![Embedding](https://img.shields.io/badge/Embedding-BGE--M3%20(1024d)-orange)
![Docker](https://img.shields.io/badge/Docker-air--gapped%20deploy-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-75%20passed-brightgreen)

A document data pipeline for **air-gapped environments** that parses, chunks, and embeds
Korean government records (ODT) into PostgreSQL + pgvector, providing semantic search
and rule-based structured field extraction.

> Built to construct a training dataset from ~14 years (2013–2026) of public-water
> occupancy/use permit records.

## Demo Results

Everything below was captured from real runs against the [three fictional demo documents](demo_data/)
included in this repository (with PDF/HWP attachments). All organizations, companies,
places, and person names in the demo documents are fictional; terminal captures show
Korean output since the pipeline processes Korean records.

**① Ingestion** — ODT parsing → chunking (tables kept whole) → BGE-M3 embedding → pgvector

![pipeline run](docs/images/pipeline-run.svg)

**② Search** — vector search by default; automatically switches to hybrid search when a
company/organization name is detected in the query

![semantic search](docs/images/hybrid-search.svg)

**③ Permit field extraction** — nine fields extracted from body text and tables by rules,
stored with a confidence score

![permit extraction](docs/images/permit-extraction.svg)

**④ Attachment collection + HWP embedded image extraction** — images pulled from the
`BinData` streams of attached HWP (OLE) files

![hwp image extraction](docs/images/hwp-image-extraction.svg)

**⑤ Output tables** — documents, chunks, attachments, and permit extractions land in
four tables

![db tables](docs/images/db-tables.svg)

**⑥ What actually lands in each table** — `documents` rows merge parsed values with
spreadsheet metadata; `document_chunks` stores the chunk text alongside its
1024-dimension pgvector embedding

![table rows](docs/images/table-rows.svg)

**⑦ Incremental ingestion** — on re-runs, unchanged documents are skipped by hash;
only modified documents are re-ingested

![incremental update](docs/images/incremental-update.svg)

## Features

- **ODT parsing** — lxml-based `content.xml` parsing; tables converted to Markdown to
  preserve structure; drafter/department/approval-date auto-extraction
- **Korean-aware semantic chunking** — splits preferentially on Korean sentence endings
  (`다.`, `니다.`); tables are never split and stay as single chunks
- **BGE-M3 embeddings** — 1024-dimension dense vectors, offline local model loading,
  thread-safe lazy initialization
- **pgvector storage + hybrid search** — IVFFlat cosine index; automatically switches to
  vector+keyword hybrid search when an organization/company name is detected in the query
- **Rule-based permit field extraction** — pulls nine fields (issuing authority, permittee,
  location, area, purpose, permit period, fee, etc.) from body text and tables into a
  dedicated table, with a confidence score
- **Attachment collection** — collects sibling PDF/HWP files next to each ODT into the
  `attachments` table, identifying the main-body PDF (`is_main_pdf`) and attachment
  order (`file_order`)
- **HWP embedded image extraction** — binary-parses the `BinData` streams of attached HWP
  (OLE compound) files and extracts embedded images to a separate directory
  (magic-byte validation, deflate decompression, size-limit guards)
- **Incremental updates** — SHA-256 file-hash comparison so only changed documents are
  reprocessed
- **Mirror synchronization** — copies results to a plain PostgreSQL server without
  pgvector (embeddings stored as `double precision[]` arrays)
- **Air-gapped deployment** — Docker image bundling scripts for servers with no internet
  and no pip

## Architecture

```mermaid
flowchart TD
    A["CLI: main.py"] -->|run| B["DocumentPipeline<br/>(src/pipeline/processor.py)"]

    B --> C["MetadataLoader<br/>Excel/CSV document list"]
    B --> D["find_odt_files()<br/>scan year directories"]
    D --> E["ThreadPoolExecutor<br/>per-file parallelism"]

    E --> F["ODTParser.parse()<br/>body/tables/attachments/drafter/date<br/>+ SHA-256 file hash"]
    F --> G{"IncrementalUpdater<br/>hash changed?"}
    G -->|No| H["skip"]
    G -->|Yes| I["SemanticDocumentChunker<br/>Korean chunking (tables kept whole)"]
    F --> J["PermitFieldExtractor<br/>rule-based 9-field extraction"]

    I --> K["BGEM3Embedder<br/>1024-dim embeddings"]
    K --> L["VectorRepository.upsert"]
    J --> L
    C --> L

    L --> M[("PostgreSQL + pgvector<br/>documents / document_chunks<br/>attachments / permit_extractions")]

    M -->|sync-to-mirror| N["MirrorRepository"]
    N --> O[("plain PostgreSQL (Server B)<br/>embedding = double precision[]<br/>permit → upsert into existing link table")]
```

### Search flow

```mermaid
flowchart LR
    A["search query"] --> B["embed_query()<br/>with instruction prefix"]
    B --> C{"proper-noun keyword detected?<br/>(particle stripping + suffix match)"}
    C -->|yes| D["hybrid_search<br/>vector similarity × w + keyword match × (1-w)"]
    C -->|no| E["similarity_search<br/>cosine similarity (IVFFlat)"]
    D --> F["chunks + document metadata"]
    E --> F
```

## Data Model

Each document decomposes into **chunks (N), attachments (N), and one permit extraction (1)**
across four tables.

```mermaid
erDiagram
    documents ||--o{ document_chunks : "1:N (cascade)"
    documents ||--o{ attachments : "1:N (cascade)"
    documents ||--o| permit_extractions : "1:1"

    documents {
        varchar doc_id PK "document ID"
        text title
        varchar dept_name "department"
        date report_date "approval date"
        varchar biz_full_path "category path"
        varchar file_hash "SHA-256 (incremental check)"
    }
    document_chunks {
        varchar chunk_id PK
        varchar doc_id FK
        varchar chunk_type "text | table"
        text text "chunk body"
        vector embedding "vector(1024) - IVFFlat"
    }
    attachments {
        int attachment_id PK
        varchar doc_id FK
        varchar file_name "PDF / HWP"
        int file_order "attachment order"
        bool is_main_pdf "main-body PDF flag"
    }
    permit_extractions {
        varchar doc_id PK "FK, 1:1"
        varchar org_nm "issuing authority"
        text ask_org_nm "permittee"
        text addr "location"
        text xtn "area"
        date permit_start_date
        date permit_end_date
        float confidence "extraction confidence"
    }
```

## Tech Stack

| Area | Technology |
|---|---|
| Language | Python 3.11+ |
| Parsing | lxml (security-hardened XML parser), olefile (HWP binary) |
| Chunking | LangChain RecursiveCharacterTextSplitter (self-contained fallback if absent) |
| Embedding | BGE-M3 (FlagEmbedding / sentence-transformers) |
| Storage | PostgreSQL 16 + pgvector (IVFFlat), SQLAlchemy ORM |
| Config | pydantic-settings (validated environment variables) |
| Deployment | Docker, docker-compose, offline image bundling (PowerShell) |
| Testing | pytest (9 modules, ~60 cases for the permit field extractor) |

## Project Layout

```
├─ main.py                     # CLI entry point (run / init-db / search / stats / sync-to-mirror)
├─ config/settings.py          # pydantic-based settings + validation
├─ src/
│  ├─ pipeline/processor.py    # DocumentPipeline — end-to-end orchestration
│  ├─ parsers/odt_parser.py    # ODT parsing (body/tables/attachments/metadata)
│  ├─ chunkers/semantic_chunker.py  # Korean-aware chunking
│  ├─ embeddings/bge_embedder.py    # BGE-M3 embeddings (+ Mock)
│  ├─ extractors/permit_field_extractor.py  # rule-based permit field extraction
│  ├─ metadata/loader.py       # Excel/CSV document-list metadata mapping
│  ├─ vectordb/
│  │  ├─ models.py             # pgvector schema (documents/chunks/attachments/permits)
│  │  ├─ repository.py         # upserts + vector/hybrid search
│  │  └─ mirror_repository.py  # mirror repository for pgvector-less PostgreSQL
│  ├─ quality/                 # date-consistency audit, log path masking
│  └─ converters/              # HWP embedded image extraction (standalone preprocessing)
├─ sync/sync_to_mirror.py      # lightweight standalone sync container (no embedding deps)
├─ scripts/build_onprem_bundle.ps1  # air-gapped deployment bundle builder
└─ tests/                      # pytest suite
```

## How the Pipeline Works

### 1. Ingestion

1. **Metadata loading** — reads the document-list Excel/CSV and indexes rows by doc_id.
   Auto-detects three header schemas (Korean / English / abbreviated); falls back to
   positional column mapping when headers are unrecognizable.
2. **ODT parsing** — extracts paragraphs, tables, and lists from `content.xml` in
   document order; tables become Markdown. Drafter, department, and approval date are
   extracted from the final approval table using a two-stage strategy (table structure →
   text patterns). Computes the file's SHA-256 hash.
3. **Incremental check** — compares against the hash stored in the DB and skips
   unchanged documents.
4. **Chunking** — text is split recursively, preferring Korean sentence-ending
   delimiters (default 1024 tokens with 128 overlap); each table becomes a single chunk.
   Uses a custom token estimator (1.5 tokens per Korean character).
5. **Permit extraction** — runs 10+ specialized extractors (collapsed-table patterns,
   Markdown-table key-value pairs, narrative-sentence regexes) over the marker-annotated
   full text. Per-field sanitizers reject false positives; issuing authorities are
   normalized against a whitelist plus URL-domain mapping.
6. **Embedding** — batch BGE-M3 embedding of chunks, with degenerate-embedding
   validation (identical-vector detection).
7. **Persistence** — upserts in the order `documents` → `permit_extractions` →
   `document_chunks` → `attachments`. Documents update by doc_id; chunks/attachments are
   replaced (delete-then-insert).

Files are processed in parallel with a `ThreadPoolExecutor`; per-file errors are
isolated so one bad file never aborts the batch.

### 2. Search

The query embedding uses the BGE-recommended instruction prefix. When a company or
organization name is detected in the query (Korean particle stripping + suffix matching
such as “산업/건설/시청”), the search automatically switches to vector+keyword hybrid;
otherwise it runs pure cosine-similarity search.

### 3. Mirror synchronization

Copies results into an external system's database where pgvector cannot be installed.

- Embeddings are converted from `Vector(1024)` to `double precision[]` so vanilla
  PostgreSQL can store them
- Permit rows are **upserted into the target system's existing link table**, which the
  sync never creates or drops (deliberately excluded from `MANAGED_MIRROR_TABLES`,
  guaranteed by tests)
- Also runs as a lightweight standalone sync container (`sync/`) independent of the
  pipeline image

### 4. Air-gapped deployment

For servers with no internet and no pip, `build_onprem_bundle.ps1` packages the app
image, the pgvector DB image, and a BGE-M3 model snapshot into a single export bundle
(including `docker-images.tar`). On the server, operation is just `docker load` +
`docker run`, with incremental ingestion scheduled via cron + `flock`.

## Security (Secure Coding)

- **CWE-20** — range/format validation on every CLI argument and environment variable
  (pydantic + manual checks)
- **CWE-22** — input files/directories restricted to allowed roots (path-traversal
  prevention)
- **CWE-209/532** — absolute paths reduced to file names in logs/error messages; DB URL
  passwords masked
- **XXE / zip-bomb defense** — external entities, DTD, and network disabled in the XML
  parser; 20 MB `content.xml` limit and 200× compression-ratio cap
- **Supply-chain defense** — embedding model restricted to the `BAAI/bge-m3` whitelist or
  existing local paths (no arbitrary remote downloads)
- **SQL injection defense** — bound parameters, LIKE escaping, integer casting

## Getting Started

### Reproduce the demo (three fictional documents included)

```bash
pip install -r requirements.txt

# start a pgvector DB
docker run -d --name demo-pgvector -e POSTGRES_DB=vectordb -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=demo -p 15499:5432 pgvector/pgvector:pg16

# ingest the demo data, then search
export DATABASE_URL=postgresql://postgres:demo@localhost:15499/vectordb
export METADATA_DOC_LIST=./demo_data/metadata
python main.py run --init-db -d demo_data/docs
python main.py search "항로 준설 협의" --top-k 3

# extract embedded images from attached HWP files only (skip the vector pipeline)
python main.py run -d demo_data/docs --extract-hwp-images --skip-pipeline
```

### Basic commands

```bash
# initialize DB + full ingestion
python main.py run --init-db

# test without GPU/model (mock embedder)
python main.py run --mock

# incremental ingestion (changed hashes only)
python main.py run

# vector search
python main.py search "공유수면 매립" --top-k 10

# DB stats
python main.py stats

# mirror DB sync
python main.py sync-to-mirror
```

For Docker-based operation and air-gapped deployment, see [HOW_TO_RUN.md](HOW_TO_RUN.md),
[ONPREM_DOCKER.md](ONPREM_DOCKER.md), and [MIRROR_SYNC.md](MIRROR_SYNC.md).

## Tests

```bash
pytest tests/
```

- ~60 cases covering permit-field extraction patterns and false-positive rejection
- Secure coding (path validation, masking, model whitelist)
- Mirror table management policy (existing link table never touched)
- Drafter-name extraction filters, HWP image extraction, metadata-loader schema compatibility
