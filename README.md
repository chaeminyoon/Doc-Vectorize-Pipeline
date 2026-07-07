# 공문서 벡터 검색 데이터 파이프라인 (ODT → pgvector)

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16%20%2B%20pgvector-4169E1?logo=postgresql&logoColor=white)
![Embedding](https://img.shields.io/badge/Embedding-BGE--M3%20(1024d)-orange)
![Docker](https://img.shields.io/badge/Docker-air--gapped%20deploy-2496ED?logo=docker&logoColor=white)
![Tests](https://img.shields.io/badge/tests-75%20passed-brightgreen)

행정 공문서(ODT)를 파싱·청킹·임베딩하여 PostgreSQL + pgvector에 적재하고,
의미 기반 검색(semantic search)과 규칙 기반 구조화 필드 추출을 제공하는
**폐쇄망(air-gapped) 환경용 문서 데이터 파이프라인**입니다.

> 공유수면 점용·사용 허가 공문 약 14개 연도(2013~2026) 분량을 학습데이터로 구축하기 위해 개발했습니다.

## 데모 실행 결과

리포지토리에 포함된 [가상 데모 공문 3건](demo_data/)을 실제로 적재·검색·추출한 결과입니다.
(문서 내 기관·기업·지명·인명은 모두 가상입니다)

**① 적재** — ODT 파싱 → 청킹(표는 통째로) → BGE-M3 임베딩 → pgvector 적재

![pipeline run](docs/images/pipeline-run.svg)

**② 검색** — 기본은 벡터 검색, 쿼리에서 기업명이 감지되면 하이브리드 검색으로 자동 전환

![semantic search](docs/images/hybrid-search.svg)

**③ 허가정보 추출** — 본문·표에서 9개 필드를 규칙 기반 추출, 신뢰도 점수와 함께 저장

![permit extraction](docs/images/permit-extraction.svg)

## 주요 기능

- **ODT 공문 파싱** — lxml 기반 `content.xml` 파싱, 표를 Markdown으로 변환해 구조 보존, 기안자/부서/결재일 자동 추출
- **한국어 특화 시맨틱 청킹** — 한국어 종결어미(`다.`, `니다.`) 우선 분할, 표는 분할하지 않고 단일 청크 유지
- **BGE-M3 임베딩** — 1024차원 dense 벡터, 오프라인 로컬 모델 로딩, 스레드 안전 지연 로딩
- **pgvector 저장 + 하이브리드 검색** — IVFFlat 코사인 인덱스, 쿼리에서 기관·회사명 감지 시 벡터+키워드 하이브리드 검색 자동 전환
- **규칙 기반 허가정보 추출** — 공문 본문/표에서 허가기관·피허가자·위치·면적·목적·허가기간·금액 등 9개 필드를 추출해 별도 테이블 적재 (신뢰도 점수 포함)
- **증분 업데이트** — 파일 SHA-256 해시 비교로 변경된 문서만 재처리
- **미러 동기화** — pgvector가 없는 일반 PostgreSQL 서버로 결과를 복사 (임베딩은 `double precision[]` 배열로 저장)
- **폐쇄망 배포** — 인터넷/pip 없는 서버를 위한 Docker 이미지 번들링 스크립트 제공

## 아키텍처

```mermaid
flowchart TD
    A["CLI: main.py"] -->|run| B["DocumentPipeline<br/>(src/pipeline/processor.py)"]

    B --> C["MetadataLoader<br/>엑셀/CSV 문서목록 로딩"]
    B --> D["find_odt_files()<br/>연도별 디렉터리 스캔"]
    D --> E["ThreadPoolExecutor<br/>파일 단위 병렬 처리"]

    E --> F["ODTParser.parse()<br/>본문/표/첨부/기안자/결재일 추출<br/>+ SHA-256 파일 해시"]
    F --> G{"IncrementalUpdater<br/>해시 변경?"}
    G -->|No| H["skip"]
    G -->|Yes| I["SemanticDocumentChunker<br/>한국어 청킹 (표는 통째로)"]
    F --> J["PermitFieldExtractor<br/>허가정보 9개 필드 규칙 추출"]

    I --> K["BGEM3Embedder<br/>1024차원 임베딩"]
    K --> L["VectorRepository.upsert"]
    J --> L
    C --> L

    L --> M[("PostgreSQL + pgvector<br/>documents / document_chunks<br/>attachments / permit_extractions")]

    M -->|sync-to-mirror| N["MirrorRepository"]
    N --> O[("일반 PostgreSQL (서버 B)<br/>embedding = double precision[]<br/>permit → 기존 연계 테이블 upsert")]
```

### 검색 흐름

```mermaid
flowchart LR
    A["search 쿼리"] --> B["embed_query()<br/>instruction prefix 부착"]
    B --> C{"고유명사 키워드 감지<br/>(조사 제거 + 접미사 매칭)"}
    C -->|있음| D["hybrid_search<br/>벡터 유사도 × w + 키워드 매칭 × (1-w)"]
    C -->|없음| E["similarity_search<br/>코사인 유사도 (IVFFlat)"]
    D --> F["청크 + 문서 메타데이터 반환"]
    E --> F
```

## 기술 스택

| 영역 | 기술 |
|---|---|
| 언어 | Python 3.11+ |
| 파싱 | lxml (보안 하드닝된 XML 파서), olefile (HWP 바이너리) |
| 청킹 | LangChain RecursiveCharacterTextSplitter (미설치 시 자체 fallback) |
| 임베딩 | BGE-M3 (FlagEmbedding / sentence-transformers) |
| 저장소 | PostgreSQL 16 + pgvector (IVFFlat), SQLAlchemy ORM |
| 설정 | pydantic-settings (환경변수 검증) |
| 배포 | Docker, docker-compose, 오프라인 이미지 번들링 (PowerShell) |
| 테스트 | pytest (9개 모듈, 허가필드 추출기 ~60 케이스) |

## 프로젝트 구조

```
├─ main.py                     # CLI 진입점 (run / init-db / search / stats / sync-to-mirror)
├─ config/settings.py          # pydantic 기반 환경변수 설정 + 검증
├─ src/
│  ├─ pipeline/processor.py    # DocumentPipeline — 전체 오케스트레이션
│  ├─ parsers/odt_parser.py    # ODT 파싱 (본문/표/첨부/메타데이터 추출)
│  ├─ chunkers/semantic_chunker.py  # 한국어 특화 청킹
│  ├─ embeddings/bge_embedder.py    # BGE-M3 임베딩 (+ Mock)
│  ├─ extractors/permit_field_extractor.py  # 허가정보 규칙 기반 추출
│  ├─ metadata/loader.py       # 엑셀/CSV 문서목록 메타데이터 매핑
│  ├─ vectordb/
│  │  ├─ models.py             # pgvector 스키마 (documents/chunks/attachments/permits)
│  │  ├─ repository.py         # upsert + 벡터/하이브리드 검색
│  │  └─ mirror_repository.py  # pgvector 없는 PG로의 미러 저장소
│  ├─ quality/                 # 날짜 정합성 감사, 로그 경로 마스킹
│  └─ converters/              # HWP 내장 이미지 추출 (독립 전처리 유틸)
├─ sync/sync_to_mirror.py      # 경량 독립 동기화 컨테이너 (임베딩 의존성 없음)
├─ scripts/build_onprem_bundle.ps1  # 폐쇄망 반출용 Docker 번들 생성
└─ tests/                      # pytest 테스트
```

## 파이프라인 동작 방식

### 1. 적재 (ingestion)

1. **메타데이터 로딩** — 문서목록 엑셀/CSV를 읽어 doc_id 기준으로 인덱싱. 한글/영문/약어 3종 헤더 스키마를 자동 감지하고, 헤더 인식 실패 시 컬럼 위치 기반 매핑으로 fallback.
2. **ODT 파싱** — `content.xml`에서 문단·표·목록을 순서 보존하며 추출. 표는 Markdown으로 변환. 마지막 결재표에서 기안자·부서·결재일을 2단계 전략(표 구조 → 텍스트 패턴)으로 추출. 파일 SHA-256 해시 계산.
3. **증분 판단** — DB에 저장된 해시와 비교해 변경 없는 문서는 스킵.
4. **청킹** — 텍스트는 한국어 종결어미를 우선 구분자로 재귀 분할(기본 1024토큰, 128 오버랩), 표는 통째로 하나의 청크. 한글 1.5토큰/문자로 추정하는 자체 토큰 카운터 사용.
5. **허가정보 추출** — 표 마커가 붙은 원문 전체에서 10개 이상의 특화 추출기(붕괴된 표 패턴, Markdown 표 key-value, 서술형 문장 정규식)를 순차 적용. 필드별 sanitize로 오탐 제거, 기관명은 화이트리스트+URL 도메인으로 정규화.
6. **임베딩** — BGE-M3로 청크 배치 임베딩. 임베딩 퇴화(전부 동일 벡터) 검증 포함.
7. **저장** — `documents` → `permit_extractions` → `document_chunks` → `attachments` 순서로 upsert. 문서는 doc_id 기준 갱신, 청크/첨부는 삭제 후 재삽입.

파일 단위 `ThreadPoolExecutor` 병렬 처리이며, 개별 파일 오류는 격리되어 전체 배치가 중단되지 않습니다.

### 2. 검색

쿼리 임베딩에 BGE 권장 instruction prefix를 붙이고, 쿼리에서 회사·기관명 고유명사가 감지되면(한국어 조사 제거 + '산업/건설/시청' 등 접미사 매칭) 벡터+키워드 하이브리드 검색으로 자동 전환합니다. 감지되지 않으면 순수 코사인 유사도 검색을 수행합니다.

### 3. 미러 동기화

pgvector를 설치할 수 없는 외부 시스템 DB로 결과를 복사합니다.

- 임베딩을 `Vector(1024)` → `double precision[]`로 변환해 순정 PostgreSQL에서도 수용
- 허가정보는 대상 시스템의 **기존 연계 테이블에 upsert**하되, 해당 테이블은 절대 생성/삭제하지 않음 (`MANAGED_MIRROR_TABLES`에서 의도적으로 제외, 테스트로 보장)
- 파이프라인 이미지와 독립된 경량 sync 컨테이너(`sync/`)로도 실행 가능

### 4. 폐쇄망 배포

인터넷·pip이 없는 서버를 위해 `build_onprem_bundle.ps1`이 앱 이미지 + pgvector DB 이미지 + BGE-M3 모델 스냅샷을 하나의 반출 번들(`docker-images.tar` 포함)로 묶습니다. 서버에서는 `docker load` 후 `docker run`만으로 운영하며, cron + `flock`으로 증분 적재를 스케줄링합니다.

## 보안 설계 (시큐어 코딩)

- **CWE-20** — CLI 인자·환경변수 전 항목 범위/형식 검증 (pydantic + 수동 검증)
- **CWE-22** — 입력 파일/디렉터리를 허용된 루트 내부로 제한 (경로 탐색 방지)
- **CWE-209/532** — 로그·에러 메시지에서 절대경로를 파일명으로 치환, DB URL 비밀번호 마스킹
- **XXE/zip-bomb 방어** — XML 외부 엔티티·DTD·네트워크 비활성화, content.xml 20MB/압축비 200배 상한
- **공급망 방어** — 임베딩 모델은 `BAAI/bge-m3` 화이트리스트 또는 로컬 경로만 허용 (임의 원격 다운로드 차단)
- **SQL 인젝션 방어** — 바인딩 파라미터, LIKE 이스케이프, 정수 캐스팅

## 실행 방법

### 데모 재현 (포함된 가상 공문 3건)

```bash
pip install -r requirements.txt

# pgvector DB 기동
docker run -d --name demo-pgvector -e POSTGRES_DB=vectordb -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=demo -p 15499:5432 pgvector/pgvector:pg16

# 데모 데이터 적재 → 검색
export DATABASE_URL=postgresql://postgres:demo@localhost:15499/vectordb
export METADATA_DOC_LIST=./demo_data/metadata
python main.py run --init-db -d demo_data/docs
python main.py search "항로 준설 협의" --top-k 3
```

### 기본 명령

```bash
# DB 초기화 + 전체 적재
python main.py run --init-db

# GPU/모델 없이 테스트 (Mock 임베더)
python main.py run --mock

# 증분 적재 (해시 변경분만)
python main.py run

# 벡터 검색
python main.py search "공유수면 매립" --top-k 10

# DB 통계
python main.py stats

# 미러 DB 동기화
python main.py sync-to-mirror
```

Docker 기반 운영·폐쇄망 배포 절차는 [HOW_TO_RUN.md](HOW_TO_RUN.md), [ONPREM_DOCKER.md](ONPREM_DOCKER.md), [MIRROR_SYNC.md](MIRROR_SYNC.md) 참고.

## 테스트

```bash
pytest tests/
```

- 허가필드 추출기 패턴/오탐 방지 ~60 케이스
- 시큐어 코딩 (경로 검증, 마스킹, 모델 화이트리스트)
- 미러 테이블 관리 정책 (기존 연계 테이블 미접촉 보장)
- 기안자명 추출 필터, HWP 이미지 추출, 메타데이터 로더 스키마 호환성
