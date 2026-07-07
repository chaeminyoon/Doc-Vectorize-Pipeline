"""
BGE-M3 임베딩 모듈
다국어(한국어/영어) 텍스트 임베딩 생성
"""
import logging
import threading
import numpy as np
from pathlib import Path
from typing import Union
from tqdm import tqdm

logger = logging.getLogger(__name__)
ALLOWED_REMOTE_EMBEDDING_MODELS = {"BAAI/bge-m3"}


class BGEM3Embedder:
    """
    BGE-M3 임베더

    BAAI/bge-m3 모델을 사용하여 텍스트 임베딩 생성
    다국어 지원 (한국어, 영어 등)
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        device: str = "cpu",
        normalize_embeddings: bool = True,
        batch_size: int = 32
    ):
        """
        Args:
            model_name: 임베딩 모델 이름
            device: 실행 디바이스 (cuda/cpu)
            normalize_embeddings: 임베딩 정규화 여부
            batch_size: 배치 크기
        """
        self.model_name = self._validate_model_name(model_name)
        self.device = device
        self.normalize_embeddings = normalize_embeddings
        self.batch_size = batch_size

        self._model = None
        self._model_type = None  # 'flag' or 'sentence_transformer'
        self._dimension = 1024  # BGE-M3 기본 차원
        self._lock = threading.Lock()

    @staticmethod
    def _validate_model_name(model_name: str) -> str:
        normalized = str(model_name).strip()
        if not normalized:
            raise ValueError("model_name must not be empty")

        candidate = Path(normalized).expanduser()
        if candidate.exists():
            return str(candidate.resolve())

        is_explicit_path = (
            normalized.startswith((".", "~", "/", "\\"))
            or (len(normalized) > 1 and normalized[1] == ":")
        )
        if is_explicit_path:
            raise ValueError(
                f"Embedding model path does not exist: {candidate.name or normalized}"
            )

        if normalized not in ALLOWED_REMOTE_EMBEDDING_MODELS:
            allowed = ", ".join(sorted(ALLOWED_REMOTE_EMBEDDING_MODELS))
            raise ValueError(
                f"Embedding model must be one of [{allowed}] or an existing local path"
            )

        return normalized

    @property
    def model(self):
        """모델 지연 로딩 (Thread-safe)"""
        if self._model is None:
            with self._lock:
                if self._model is None:  # Double-checked locking
                    self._load_model()
        return self._model

    @property
    def dimension(self) -> int:
        """임베딩 벡터 차원"""
        return self._dimension

    def _load_model(self):
        """모델 로드"""
        try:
            # FlagEmbedding 사용 시도 (더 효율적)
            from FlagEmbedding import BGEM3FlagModel

            self._model = BGEM3FlagModel(
                self.model_name,
                use_fp16=True if self.device == "cuda" else False,
                device=self.device
            )
            self._model_type = 'flag'
            logger.info(f"Loaded BGE-M3 model with FlagEmbedding: {self.model_name} (device={self.device})")

        except ImportError:
            # sentence-transformers fallback
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(
                self.model_name,
                device=self.device
            )
            self._model_type = 'sentence_transformer'
            logger.info(f"Loaded BGE-M3 model with sentence-transformers: {self.model_name} (device={self.device})")

    def embed(self, texts: Union[str, list[str]], show_progress: bool = False) -> np.ndarray:
        """
        텍스트 임베딩 생성

        Args:
            texts: 단일 텍스트 또는 텍스트 리스트
            show_progress: 진행률 표시 여부

        Returns:
            임베딩 벡터 (2D numpy array)
        """
        if isinstance(texts, str):
            texts = [texts]

        if not texts:
            return np.array([])

        # 배치 처리
        all_embeddings = []

        if show_progress:
            iterator = tqdm(
                range(0, len(texts), self.batch_size),
                desc="Embedding",
                total=(len(texts) + self.batch_size - 1) // self.batch_size
            )
        else:
            iterator = range(0, len(texts), self.batch_size)

        for i in iterator:
            batch = texts[i:i + self.batch_size]
            batch_embeddings = self._embed_batch(batch)
            all_embeddings.append(batch_embeddings)

        embeddings = np.vstack(all_embeddings)

        # 임베딩 품질 검증
        if embeddings.shape[0] > 1:
            self._validate_embeddings(embeddings, len(texts))

        return embeddings

    def _validate_embeddings(self, embeddings: np.ndarray, num_texts: int):
        """임베딩 퇴화(degeneration) 검증"""
        # 첫 번째 임베딩과 나머지 비교하여 동일 벡터 수 측정
        diffs = np.abs(embeddings[1:] - embeddings[0]).sum(axis=1)
        identical_count = int(np.sum(diffs < 1e-6)) + 1
        if identical_count > 1:
            unique_ratio = 1.0 - (identical_count / num_texts)
            logger.warning(
                f"Embedding degeneration detected: {identical_count}/{num_texts} "
                f"embeddings identical to first vector (unique ratio: {unique_ratio:.1%})"
            )
            if identical_count == num_texts:
                raise ValueError(
                    f"All {num_texts} embeddings are identical. "
                    f"Model may not be loaded correctly or input is degenerate."
                )

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        """배치 임베딩"""
        # model 프로퍼티 접근으로 lazy loading 보장 (_model_type 설정됨)
        model = self.model

        with self._lock:
            if self._model_type == 'flag':
                result = model.encode(
                    texts,
                    return_dense=True,
                    return_sparse=False,
                    return_colbert_vecs=False
                )
                embeddings = result['dense_vecs']
                if not isinstance(embeddings, np.ndarray):
                    embeddings = np.array(embeddings)
                if self.normalize_embeddings:
                    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
                    norms = np.maximum(norms, 1e-12)  # 0 나눗셈 방지
                    embeddings = embeddings / norms
                return embeddings
            else:
                # sentence-transformers 사용
                embeddings = model.encode(
                    texts,
                    normalize_embeddings=self.normalize_embeddings,
                    show_progress_bar=False
                )
                return embeddings

    def embed_query(self, query: str) -> np.ndarray:
        """
        검색 쿼리 임베딩 (Instruction prefix 추가)

        Args:
            query: 검색 쿼리

        Returns:
            임베딩 벡터 (1D numpy array)
        """
        # BGE 모델은 쿼리에 instruction prefix를 붙이면 성능 향상
        instruction = "Represent this sentence for searching relevant passages: "
        prefixed_query = instruction + query

        embeddings = self.embed([prefixed_query])
        return embeddings[0]

    def similarity(self, query_embedding: np.ndarray, doc_embeddings: np.ndarray) -> np.ndarray:
        """
        코사인 유사도 계산

        Args:
            query_embedding: 쿼리 임베딩 (1D)
            doc_embeddings: 문서 임베딩들 (2D)

        Returns:
            유사도 점수 배열
        """
        # 정규화된 벡터이므로 내적이 곧 코사인 유사도
        return np.dot(doc_embeddings, query_embedding)


class MockEmbedder:
    """테스트용 Mock 임베더 — 텍스트 내용 기반으로 고유 벡터 생성"""

    def __init__(self, dimension: int = 1024):
        self._dimension = dimension

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Union[str, list[str]], show_progress: bool = False) -> np.ndarray:
        if isinstance(texts, str):
            texts = [texts]

        embeddings = np.zeros((len(texts), self._dimension))
        for i, text in enumerate(texts):
            # 텍스트 내용의 hash를 seed로 사용하여 재현 가능하면서도 고유한 벡터 생성
            text_hash = hash(text) % (2**32)
            rng = np.random.RandomState(text_hash)
            embeddings[i] = rng.randn(self._dimension)

        # 정규화
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
        norms = np.maximum(norms, 1e-12)
        embeddings = embeddings / norms
        return embeddings

    def embed_query(self, query: str) -> np.ndarray:
        return self.embed([query])[0]


if __name__ == "__main__":
    # 테스트 (실제 모델 없이)
    print("Testing MockEmbedder...")
    embedder = MockEmbedder(dimension=1024)

    texts = [
        "공유수면 매립기본계획 반영요청에 대한 심의결과입니다.",
        "국가어항 개발계획과 관련된 문서입니다.",
        "2023년 상반기 현장실사 계획을 알려드립니다."
    ]

    embeddings = embedder.embed(texts)
    print(f"Embedding shape: {embeddings.shape}")
    print(f"First embedding (first 5 dims): {embeddings[0][:5]}")

    # 유사도 테스트
    query = "매립 계획 심의"
    query_emb = embedder.embed_query(query)
    similarities = np.dot(embeddings, query_emb)
    print(f"\nQuery: {query}")
    print(f"Similarities: {similarities}")
