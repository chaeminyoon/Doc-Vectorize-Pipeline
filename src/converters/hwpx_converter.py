"""
HWPX/HWP → PDF 변환 모듈
LibreOffice를 사용하여 HWPX/HWP 파일을 PDF로 변환

사용법:
    converter = HWPXConverter()
    results = converter.convert_directory(Path("/data/documents"))
"""
import subprocess
import shutil
import sys
import logging
import time
import tempfile
import concurrent.futures
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from enum import Enum

logger = logging.getLogger(__name__)


class ConversionStatus(Enum):
    """변환 상태"""
    SUCCESS = "success"
    SKIPPED = "skipped"      # 이미 PDF 존재
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class ConversionResult:
    """단일 파일 변환 결과"""
    source_path: Path
    pdf_path: Optional[Path]
    status: ConversionStatus
    message: str = ""

    def __str__(self):
        return f"[{self.status.value.upper()}] {self.source_path.name} → {self.pdf_path or 'N/A'}"


@dataclass
class BatchConversionResult:
    """일괄 변환 결과"""
    total: int = 0
    success: int = 0
    skipped: int = 0
    failed: int = 0
    timeout: int = 0
    results: list[ConversionResult] = None

    def __post_init__(self):
        if self.results is None:
            self.results = []

    def __str__(self):
        return (
            f"BatchConversionResult(\n"
            f"  total={self.total}, success={self.success}, "
            f"skipped={self.skipped}, failed={self.failed}, timeout={self.timeout}\n"
            f")"
        )


# LibreOffice 기본 경로 (플랫폼별)
DEFAULT_SOFFICE_PATHS = {
    "linux": [
        "/opt/libreoffice7.6/program/soffice",
        "/opt/libreoffice24.8/program/soffice",
        "/usr/bin/soffice",
        "/usr/bin/libreoffice",
    ],
    "win32": [
        r"C:\Program Files\LibreOffice\program\soffice.exe",
        r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
    ],
    "darwin": [
        "/Applications/LibreOffice.app/Contents/MacOS/soffice",
    ],
}

ALLOWED_SOFFICE_NAMES = {"soffice", "soffice.exe", "soffice.bin"}

# 변환 대상 확장자
HWPX_EXTENSIONS = {".hwp", ".hwpx"}


class HWPXConverter:
    """
    HWPX/HWP → PDF 변환기 (LibreOffice 기반)

    LibreOffice의 headless 모드를 사용하여 HWPX/HWP 파일을 PDF로 변환합니다.
    이미 변환된 파일은 자동으로 스킵합니다.

    Args:
        soffice_path: LibreOffice 실행 파일 경로 (None이면 자동 탐지)
        timeout: 파일당 변환 타임아웃 (초)
        delay: 연속 변환 시 딜레이 (초, LibreOffice 안정화)
        force: True면 기존 PDF 덮어쓰기
    """

    def __init__(
        self,
        soffice_path: Optional[str] = None,
        timeout: int = 180,
        force: bool = False,
        max_workers: Optional[int] = None,
    ):
        candidate_path = soffice_path or self._find_soffice()
        self.soffice_path = (
            self._validate_soffice_path(candidate_path)
            if candidate_path
            else None
        )
        self.timeout = timeout
        self.force = force
        
        # CPU 코어 수 기반 병렬 워커 설정 (너무 많으면 메모리 부족 발생할 수 있으므로 최대 8로 제한)
        default_workers = min(8, max(2, (os.cpu_count() or 4) - 1))
        self.max_workers = max_workers or default_workers

        if not self.soffice_path:
            raise FileNotFoundError(
                "LibreOffice(soffice)를 찾을 수 없습니다. "
                "SOFFICE_PATH 환경변수를 설정하거나, "
                "soffice_path 인자로 경로를 지정해주세요."
            )

        logger.info(f"LibreOffice 경로: {self.soffice_path}")

    @staticmethod
    def _validate_soffice_path(path_str: str) -> str:
        candidate = Path(path_str).expanduser()
        resolved = candidate.resolve(strict=True)

        if not resolved.is_file():
            raise FileNotFoundError(f"LibreOffice 실행 파일이 아닙니다: {resolved}")

        if resolved.name.lower() not in ALLOWED_SOFFICE_NAMES:
            raise ValueError(f"허용되지 않는 LibreOffice 실행 파일명입니다: {resolved.name}")

        return str(resolved)

    def _find_soffice(self) -> Optional[str]:
        """LibreOffice 실행 파일 자동 탐지"""
        # 자동 탐지는 플랫폼별 기본 설치 경로만 신뢰한다.
        platform = sys.platform
        if platform.startswith("linux"):
            candidates = DEFAULT_SOFFICE_PATHS["linux"]
        elif platform == "win32":
            candidates = DEFAULT_SOFFICE_PATHS["win32"]
        elif platform == "darwin":
            candidates = DEFAULT_SOFFICE_PATHS["darwin"]
        else:
            candidates = []

        for path in candidates:
            try:
                return self._validate_soffice_path(path)
            except (FileNotFoundError, OSError, ValueError):
                continue

        return None

    def convert_file(self, src: Path, outdir: Optional[Path] = None) -> ConversionResult:
        """
        단일 HWPX/HWP 파일을 PDF로 변환

        Args:
            src: 원본 파일 경로
            outdir: 출력 디렉토리 (None이면 원본과 같은 디렉토리)

        Returns:
            ConversionResult
        """
        src = Path(src)

        if not src.exists():
            return ConversionResult(
                source_path=src,
                pdf_path=None,
                status=ConversionStatus.FAILED,
                message=f"파일이 존재하지 않습니다: {src}"
            )

        if src.suffix.lower() not in HWPX_EXTENSIONS:
            return ConversionResult(
                source_path=src,
                pdf_path=None,
                status=ConversionStatus.FAILED,
                message=f"지원하지 않는 확장자: {src.suffix}"
            )

        outdir = outdir or src.parent
        pdf_path = outdir / (src.stem + ".pdf")

        # 이미 변환된 파일 스킵
        if pdf_path.exists() and not self.force:
            logger.info(f"[SKIP] 이미 존재: {pdf_path}")
            return ConversionResult(
                source_path=src,
                pdf_path=pdf_path,
                status=ConversionStatus.SKIPPED,
                message="PDF가 이미 존재합니다"
            )

        # LibreOffice 명령어 구성
        cmd = [
            self.soffice_path,
            "--headless",
            "--nologo",
            "--nofirststartwizard",
            "--convert-to", "pdf",
            str(src),
            "--outdir", str(outdir),
        ]

        logger.info(f"[CONVERT] {src.name}")

        try:
            # Popen을 사용하여 타임아웃 시 프로세스를 확실하게 kill
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            try:
                stdout, _ = proc.communicate(timeout=self.timeout)
                output = stdout.decode(errors="ignore").strip() if stdout else ""
            except subprocess.TimeoutExpired:
                # 타임아웃 시 프로세스 트리 강제 종료
                proc.kill()
                proc.communicate()  # 잔여 출력 정리
                logger.warning(f"[TIMEOUT] 변환 시간 초과 ({self.timeout}초): {src.name}")
                return ConversionResult(
                    source_path=src,
                    pdf_path=None,
                    status=ConversionStatus.TIMEOUT,
                    message=f"변환 시간 초과 ({self.timeout}초)"
                )

            if pdf_path.exists():
                logger.info(f"[OK] 생성됨: {pdf_path.name}")
                return ConversionResult(
                    source_path=src,
                    pdf_path=pdf_path,
                    status=ConversionStatus.SUCCESS,
                    message=output
                )
            else:
                logger.warning(f"[FAIL] PDF 미생성: {src.name}")
                return ConversionResult(
                    source_path=src,
                    pdf_path=None,
                    status=ConversionStatus.FAILED,
                    message=f"PDF 미생성. LibreOffice 출력: {output}"
                )

        except (OSError, subprocess.SubprocessError) as e:
            logger.error(f"[ERROR] 변환 중 오류: {src.name} - {e}")
            return ConversionResult(
                source_path=src,
                pdf_path=None,
                status=ConversionStatus.FAILED,
                message=str(e)
            )

    def convert_directory(
        self,
        root_dir: Path,
        outdir: Optional[Path] = None,
        show_progress: bool = True,
    ) -> BatchConversionResult:
        """
        디렉토리 내 모든 HWPX/HWP 파일을 PDF로 변환

        Args:
            root_dir: 검색 시작 디렉토리
            outdir: 출력 디렉토리 (None이면 각 파일의 원래 디렉토리)
            show_progress: 진행률 표시 여부

        Returns:
            BatchConversionResult
        """
        root_dir = Path(root_dir)
        if not root_dir.exists():
            logger.error(f"경로가 존재하지 않습니다: {root_dir}")
            return BatchConversionResult()

        # HWPX/HWP 파일 검색
        hwpx_files = [
            p for p in root_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in HWPX_EXTENSIONS
        ]

        batch_result = BatchConversionResult(total=len(hwpx_files))
        logger.info(f"변환 대상 HWPX/HWP 파일 수: {len(hwpx_files)}")

        if not hwpx_files:
            return batch_result

        # 진행률 표시
        try:
            from tqdm import tqdm
            iterator = tqdm(hwpx_files, desc="HWPX→PDF 변환") if show_progress else hwpx_files
        except ImportError:
            iterator = hwpx_files

        for src in iterator:
            result = self.convert_file(src, outdir=outdir)
            batch_result.results.append(result)

            if result.status == ConversionStatus.SUCCESS:
                batch_result.success += 1
            elif result.status == ConversionStatus.SKIPPED:
                batch_result.skipped += 1
            elif result.status == ConversionStatus.TIMEOUT:
                batch_result.timeout += 1
            else:
                batch_result.failed += 1

            # LibreOffice 안정화 딜레이 (실제 변환 시도한 경우에만, 스킵 시에는 불필요)
            if result.status != ConversionStatus.SKIPPED:
                time.sleep(1.5)

        return batch_result

    def convert_directories(
        self,
        directories: list[Path],
        show_progress: bool = True,
    ) -> BatchConversionResult:
        """
        여러 디렉토리의 HWPX/HWP 파일 일괄 변환

        Args:
            directories: 디렉토리 경로 목록
            show_progress: 진행률 표시 여부

        Returns:
            BatchConversionResult (합산 결과)
        """
        combined = BatchConversionResult()

        for directory in directories:
            result = self.convert_directory(
                directory, show_progress=show_progress
            )
            combined.total += result.total
            combined.success += result.success
            combined.skipped += result.skipped
            combined.failed += result.failed
            combined.timeout += result.timeout
            combined.results.extend(result.results)

        return combined


def find_hwpx_files(base_dir: str | Path) -> list[Path]:
    """
    디렉토리에서 모든 HWPX/HWP 파일 찾기

    Args:
        base_dir: 검색 시작 디렉토리

    Returns:
        HWPX/HWP 파일 경로 목록
    """
    base_path = Path(base_dir)
    return [
        p for p in base_path.rglob("*")
        if p.is_file() and p.suffix.lower() in HWPX_EXTENSIONS
    ]


if __name__ == "__main__":
    # 독립 실행 테스트
    import sys

    if len(sys.argv) < 2:
        print("사용법: python hwpx_converter.py <대상_폴더> [--force]")
        sys.exit(1)

    logging.basicConfig(level=logging.INFO)

    target = Path(sys.argv[1])
    force = "--force" in sys.argv

    converter = HWPXConverter(force=force)
    result = converter.convert_directory(target)
    print(f"\n{result}")
