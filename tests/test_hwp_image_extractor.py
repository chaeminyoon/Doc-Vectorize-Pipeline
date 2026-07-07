from __future__ import annotations

import sys
import types
from pathlib import Path

from src.converters.hwp_image_extractor import (
    HWPImageExtractor,
    STATUS_EXTRACTED,
    STATUS_FAILED,
)


PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n"
    b"\x00\x00\x00\rIHDR"
    b"\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x02\x00\x00\x00"
    b"\x90wS\xde"
)


def _raw_deflate(data: bytes) -> bytes:
    import zlib

    compressor = zlib.compressobj(wbits=-15)
    return compressor.compress(data) + compressor.flush()


class _Stream:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeOle:
    def __init__(self, image_name: str, image_data: bytes):
        self._image_entry = ("BinData", image_name)
        self._image_data = image_data

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def openstream(self, entry):
        if entry == "FileHeader":
            return _Stream(bytes(40))
        if tuple(entry) == self._image_entry:
            return _Stream(self._image_data)
        raise OSError(f"unexpected stream: {entry}")

    def listdir(self):
        return [list(self._image_entry)]

    def get_size(self, entry) -> int:
        if tuple(entry) != self._image_entry:
            raise OSError(f"unexpected stream: {entry}")
        return len(self._image_data)


def _install_fake_olefile(monkeypatch, image_name: str, image_data: bytes):
    module = types.SimpleNamespace(
        isOleFile=lambda _path: True,
        OleFileIO=lambda _path: _FakeOle(image_name, image_data),
    )
    monkeypatch.setitem(sys.modules, "olefile", module)


def test_extract_file_keeps_valid_raw_image_payload(tmp_path, monkeypatch):
    hwp_path = tmp_path / "sample.hwp"
    hwp_path.write_bytes(b"hwp")
    _install_fake_olefile(monkeypatch, "BIN0001.png", PNG_BYTES)

    extractor = HWPImageExtractor()

    result = extractor.extract_file(hwp_path)

    assert result.status == STATUS_EXTRACTED
    assert (result.output_dir / "BIN0001.png").read_bytes() == PNG_BYTES


def test_extract_file_rejects_invalid_non_image_payload(tmp_path, monkeypatch):
    hwp_path = tmp_path / "sample.hwp"
    hwp_path.write_bytes(b"hwp")
    _install_fake_olefile(monkeypatch, "BIN0001.png", b"not-a-png-and-not-deflate")

    extractor = HWPImageExtractor()

    result = extractor.extract_file(hwp_path)

    assert result.status == STATUS_FAILED
    assert result.output_dir.exists() is False
    assert "invalid" in result.message.lower()


def test_extract_file_decompresses_valid_deflated_image_payload(tmp_path, monkeypatch):
    hwp_path = tmp_path / "sample.hwp"
    hwp_path.write_bytes(b"hwp")
    _install_fake_olefile(monkeypatch, "BIN0001.png", _raw_deflate(PNG_BYTES))

    extractor = HWPImageExtractor()

    result = extractor.extract_file(hwp_path)

    assert result.status == STATUS_EXTRACTED
    assert (result.output_dir / "BIN0001.png").read_bytes() == PNG_BYTES
