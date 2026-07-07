"""Extract embedded images from HWP files."""

from __future__ import annotations

import logging
import shutil
import struct
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

IMAGE_EXTS = {".bmp", ".jpg", ".jpeg", ".png", ".gif", ".tif", ".tiff", ".emf", ".wmf"}
HWPTAG_BEGIN = 0x0010
HWPTAG_BIN_DATA = HWPTAG_BEGIN + 2

STATUS_EXTRACTED = "extracted"
STATUS_SKIPPED = "skipped"
STATUS_NO_IMAGES = "no_images"
STATUS_FAILED = "failed"


@dataclass
class ImageExtractionResult:
    source_path: Path
    status: str
    extracted_images: int = 0
    output_dir: Optional[Path] = None
    message: str = ""


@dataclass
class BatchImageExtractionResult:
    total_files: int = 0
    extracted_files: int = 0
    skipped_files: int = 0
    failed_files: int = 0
    no_image_files: int = 0
    total_images: int = 0
    results: list[ImageExtractionResult] = field(default_factory=list)

    def __str__(self) -> str:
        return (
            "BatchImageExtractionResult(\n"
            f"  total_files={self.total_files},\n"
            f"  extracted_files={self.extracted_files},\n"
            f"  skipped_files={self.skipped_files},\n"
            f"  failed_files={self.failed_files},\n"
            f"  no_image_files={self.no_image_files},\n"
            f"  total_images={self.total_images}\n"
            ")"
        )


class HWPImageExtractor:
    """Extract embedded images referenced from HWP DocInfo/BinData."""
    MAX_BIN_STREAM_SIZE = 32 * 1024 * 1024
    MAX_IMAGE_OUTPUT_SIZE = 64 * 1024 * 1024
    MAX_TOTAL_IMAGE_OUTPUT_SIZE = 256 * 1024 * 1024

    @staticmethod
    def _parse_docinfo_refs(ole, compressed: bool) -> dict[int, str]:
        try:
            docinfo = ole.openstream("DocInfo").read()
        except OSError:
            return {}

        if compressed:
            try:
                docinfo = zlib.decompress(docinfo, -15)
            except zlib.error:
                return {}

        offset = 0
        bin_map: dict[int, str] = {}

        while offset < len(docinfo):
            if offset + 4 > len(docinfo):
                break

            header = struct.unpack_from("<I", docinfo, offset)[0]
            tag_id = header & 0x3FF
            size = (header >> 20) & 0xFFF

            if size == 0xFFF:
                if offset + 8 > len(docinfo):
                    break
                size = struct.unpack_from("<I", docinfo, offset + 4)[0]
                offset += 8
            else:
                offset += 4

            if tag_id == HWPTAG_BIN_DATA and size >= 6:
                data = docinfo[offset:offset + size]
                prop = struct.unpack_from("<H", data, 0)[0]
                type_value = prop & 0x000F

                if type_value == 1:
                    bin_id = struct.unpack_from("<H", data, 2)[0]
                    ext_len = struct.unpack_from("<H", data, 4)[0]
                    ext_value = data[6:6 + ext_len * 2].decode("utf-16-le", errors="ignore")
                    bin_map[bin_id] = ext_value.upper()

            offset += size

        return bin_map

    @staticmethod
    def _build_referenced_names(ref_map: dict[int, str]) -> set[str]:
        referenced: set[str] = set()
        for bin_id, ext in ref_map.items():
            referenced.add(f"BIN{bin_id:04X}.{ext}")
            referenced.add(f"BIN{bin_id:04X}.{ext.lower()}")
            referenced.add(f"BIN{bin_id:04x}.{ext}")
            referenced.add(f"BIN{bin_id:04x}.{ext.lower()}")
        return referenced

    @staticmethod
    def _looks_like_image_payload(filename: str, data: bytes) -> bool:
        if not data:
            return False

        extension = Path(filename).suffix.lower()
        prefix_map = {
            ".bmp": (b"BM",),
            ".jpg": (b"\xff\xd8\xff",),
            ".jpeg": (b"\xff\xd8\xff",),
            ".png": (b"\x89PNG\r\n\x1a\n",),
            ".gif": (b"GIF87a", b"GIF89a"),
            ".tif": (b"II*\x00", b"MM\x00*"),
            ".tiff": (b"II*\x00", b"MM\x00*"),
        }

        if extension in prefix_map:
            return any(data.startswith(prefix) for prefix in prefix_map[extension])
        if extension == ".emf":
            return len(data) >= 44 and data[:4] == b"\x01\x00\x00\x00" and data[40:44] == b" EMF"
        if extension == ".wmf":
            return data.startswith((b"\xd7\xcd\xc6\x9a", b"\x01\x00\x09\x00", b"\x02\x00\x09\x00"))
        return False

    def _decode_image_stream(
        self,
        source_path: Path,
        output_dir: Path,
        filename: str,
        raw_data: bytes,
    ) -> tuple[Optional[bytes], Optional[ImageExtractionResult]]:
        if not raw_data:
            return None, ImageExtractionResult(
                source_path=source_path,
                status=STATUS_FAILED,
                output_dir=output_dir,
                message="Embedded image stream is empty.",
            )

        if self._looks_like_image_payload(filename, raw_data):
            return raw_data, None

        try:
            data = zlib.decompress(raw_data, -15)
        except zlib.error as exc:
            logger.warning(
                "Invalid embedded image payload in %s (%s): %s",
                source_path.name,
                filename,
                exc,
            )
            return None, ImageExtractionResult(
                source_path=source_path,
                status=STATUS_FAILED,
                output_dir=output_dir,
                message="Embedded image payload is invalid or decompression failed.",
            )

        if not self._looks_like_image_payload(filename, data):
            return None, ImageExtractionResult(
                source_path=source_path,
                status=STATUS_FAILED,
                output_dir=output_dir,
                message="Embedded image payload is invalid after decompression.",
            )

        return data, None

    @staticmethod
    def _iter_hwp_files(root_dir: Path) -> list[Path]:
        return sorted(path for path in root_dir.rglob("*.hwp") if path.is_file())

    @staticmethod
    def _is_safe_cleanup_dir(root_dir: str | Path, candidate_dir: str | Path) -> bool:
        root_path = Path(root_dir)
        candidate_path = Path(candidate_dir)

        try:
            root_resolved = root_path.resolve(strict=True)
            candidate_resolved = candidate_path.resolve(strict=True)
        except (OSError, RuntimeError):
            return False

        if not candidate_path.is_dir() or candidate_path.is_symlink():
            return False
        if candidate_resolved == root_resolved:
            return False

        try:
            candidate_resolved.relative_to(root_resolved)
        except ValueError:
            return False
        return True

    def extract_file(self, hwp_path: str | Path) -> ImageExtractionResult:
        import olefile

        source_path = Path(hwp_path)
        output_dir = source_path.with_name(f"{source_path.stem}_images")

        if not source_path.exists():
            return ImageExtractionResult(
                source_path=source_path,
                status=STATUS_FAILED,
                output_dir=output_dir,
                message="HWP file does not exist.",
            )

        if output_dir.exists():
            return ImageExtractionResult(
                source_path=source_path,
                status=STATUS_SKIPPED,
                output_dir=output_dir,
                message="Image directory already exists.",
            )

        if not olefile.isOleFile(str(source_path)):
            return ImageExtractionResult(
                source_path=source_path,
                status=STATUS_FAILED,
                output_dir=output_dir,
                message="File is not a valid OLE HWP document.",
            )

        try:
            with olefile.OleFileIO(str(source_path)) as ole:
                file_header = ole.openstream("FileHeader").read()
                if len(file_header) < 40:
                    return ImageExtractionResult(
                        source_path=source_path,
                        status=STATUS_FAILED,
                        output_dir=output_dir,
                        message="FileHeader is too short.",
                    )

                flags = struct.unpack_from("<I", file_header, 36)[0]
                compressed = bool(flags & 0x01)
                ref_map = self._parse_docinfo_refs(ole, compressed)
                referenced = self._build_referenced_names(ref_map)

                images: list[tuple[str, list[str]]] = []
                for entry in ole.listdir():
                    if not entry or entry[0] != "BinData" or len(entry) < 2:
                        continue

                    filename = Path(entry[-1]).name
                    extension = Path(filename).suffix.lower()

                    if extension not in IMAGE_EXTS:
                        continue
                    if ".." in filename or "\x00" in filename:
                        continue
                    if referenced and filename not in referenced:
                        continue

                    images.append((filename, entry))

                if not images:
                    return ImageExtractionResult(
                        source_path=source_path,
                        status=STATUS_NO_IMAGES,
                        output_dir=output_dir,
                    )

                prepared_images: list[tuple[str, bytes]] = []
                total_output_size = 0

                for filename, entry in images:
                    stream_size = ole.get_size(entry)
                    if stream_size > self.MAX_BIN_STREAM_SIZE:
                        return ImageExtractionResult(
                            source_path=source_path,
                            status=STATUS_FAILED,
                            output_dir=output_dir,
                            message="Embedded image stream is too large.",
                        )

                    raw_data = ole.openstream(entry).read()
                    data, decode_failure = self._decode_image_stream(
                        source_path=source_path,
                        output_dir=output_dir,
                        filename=filename,
                        raw_data=raw_data,
                    )
                    if decode_failure is not None:
                        return decode_failure

                    if len(data) > self.MAX_IMAGE_OUTPUT_SIZE:
                        return ImageExtractionResult(
                            source_path=source_path,
                            status=STATUS_FAILED,
                            output_dir=output_dir,
                            message="Embedded image is too large after decompression.",
                        )

                    total_output_size += len(data)
                    if total_output_size > self.MAX_TOTAL_IMAGE_OUTPUT_SIZE:
                        return ImageExtractionResult(
                            source_path=source_path,
                            status=STATUS_FAILED,
                            output_dir=output_dir,
                            message="Total embedded image size is too large.",
                        )

                    prepared_images.append((filename, data))

                output_dir.mkdir(exist_ok=True)
                for filename, data in prepared_images:
                    (output_dir / filename).write_bytes(data)

                return ImageExtractionResult(
                    source_path=source_path,
                    status=STATUS_EXTRACTED,
                    extracted_images=len(images),
                    output_dir=output_dir,
                )

        except PermissionError:
            logger.warning("Permission denied while extracting images from %s", source_path)
        except OSError as exc:
            logger.warning("OS error while extracting images from %s: %s", source_path, exc)
        except Exception:
            logger.exception("Unexpected error while extracting images from %s", source_path)

        return ImageExtractionResult(
            source_path=source_path,
            status=STATUS_FAILED,
            output_dir=output_dir,
            message="Image extraction failed.",
        )

    def extract_directory(
        self,
        root_dir: str | Path,
        show_progress: bool = True,
    ) -> BatchImageExtractionResult:
        root_path = Path(root_dir)
        if not root_path.exists():
            logger.warning("Directory does not exist for image extraction: %s", root_path)
            return BatchImageExtractionResult()

        logger.info("Scanning for *.hwp files in %s (This may take a moment...)", root_path)
        hwp_files = self._iter_hwp_files(root_path)
        logger.info("Found %d HWP files for image extraction.", len(hwp_files))
        result = BatchImageExtractionResult(total_files=len(hwp_files))

        if not hwp_files:
            return result

        iterator = hwp_files
        if show_progress:
            try:
                from tqdm import tqdm
                iterator = tqdm(hwp_files, desc=f"HWP images ({root_path.name})")
            except ImportError:
                iterator = hwp_files

        for hwp_file in iterator:
            file_result = self.extract_file(hwp_file)
            result.results.append(file_result)

            if file_result.status == STATUS_EXTRACTED:
                result.extracted_files += 1
                result.total_images += file_result.extracted_images
            elif file_result.status == STATUS_SKIPPED:
                result.skipped_files += 1
            elif file_result.status == STATUS_FAILED:
                result.failed_files += 1
            else:
                result.no_image_files += 1

        return result

    def extract_directories(
        self,
        directories: list[str | Path],
        show_progress: bool = True,
    ) -> BatchImageExtractionResult:
        combined = BatchImageExtractionResult()

        for directory in directories:
            result = self.extract_directory(directory, show_progress=show_progress)
            combined.total_files += result.total_files
            combined.extracted_files += result.extracted_files
            combined.skipped_files += result.skipped_files
            combined.failed_files += result.failed_files
            combined.no_image_files += result.no_image_files
            combined.total_images += result.total_images
            combined.results.extend(result.results)

        return combined

    def clean_directory(
        self,
        root_dir: str | Path,
        show_progress: bool = True,
    ) -> int:
        root_path = Path(root_dir)
        if not root_path.exists():
            logger.warning("Directory does not exist for cleanup: %s", root_path)
            return 0

        logger.info("Scanning for *_images directories in %s (This may take a moment...)", root_path)
        image_dirs = sorted(path for path in root_path.rglob("*_images") if path.is_dir())
        logger.info("Found %d image directories to clean. Starting fast parallel cleanup...", len(image_dirs))
        
        if not image_dirs:
            return 0

        import concurrent.futures

        def _remove_dir(image_dir: Path) -> bool:
            if not self._is_safe_cleanup_dir(root_path, image_dir):
                logger.warning("Skipping unsafe cleanup target: %s", image_dir)
                return False
            try:
                shutil.rmtree(image_dir)
                return True
            except OSError as exc:
                logger.warning("Failed to remove %s: %s", image_dir, exc)
                return False

        deleted = 0
        with concurrent.futures.ThreadPoolExecutor() as executor:
            if show_progress:
                try:
                    from tqdm import tqdm
                    results = list(tqdm(executor.map(_remove_dir, image_dirs), total=len(image_dirs), desc=f"HWP cleanup ({root_path.name})"))
                except ImportError:
                    results = list(executor.map(_remove_dir, image_dirs))
            else:
                results = list(executor.map(_remove_dir, image_dirs))

        deleted = sum(1 for r in results if r)
        logger.info("Cleanup complete. Deleted %d directories.", deleted)
        return deleted

    def clean_directories(
        self,
        directories: list[str | Path],
        show_progress: bool = True,
    ) -> int:
        return sum(
            self.clean_directory(directory, show_progress=show_progress)
            for directory in directories
        )
