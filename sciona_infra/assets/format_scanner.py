from __future__ import annotations

import hashlib
import json
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

_PREFIX_SIZE = 1024 * 1024  # 1 MB
_JSON_MAX_SIZE = 256 * 1024 * 1024  # 256 MB

ALLOWED_FORMATS = {
    "safetensors",
    "onnx",
    "json",
    "jsonl",
    "parquet",
    "npy",
    "npz",
    "txt",
    "vocab",
}

BLOCKED_MAGIC_BYTES = {
    b"\x80\x05": "pickle_v5",
    b"\x80\x04": "pickle_v4",
    b"\x80\x03": "pickle_v3",
    b"\x80\x02": "pickle_v2",
}

BLOCKED_EXTENSIONS = {".pkl", ".pickle", ".joblib"}

TORCH_LOAD_MARKERS = (
    b"torch._utils",
    b"torch.storage",
    b"torch\n",
    b"PyTorch",
    b"PYTORCH",
)

EXTENSION_FORMATS = {
    ".safetensors": "safetensors",
    ".onnx": "onnx",
    ".json": "json",
    ".jsonl": "jsonl",
    ".parquet": "parquet",
    ".npy": "npy",
    ".npz": "npz",
    ".txt": "txt",
    ".vocab": "vocab",
}


@dataclass(frozen=True)
class ScanResult:
    path: Path
    declared_format: str
    ok: bool
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(chunk_size), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: Path, expected: str) -> bool:
    return sha256_file(path) == expected.lower()


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def canonical_manifest_hash(manifest: Mapping[str, Any] | Path) -> str:
    if isinstance(manifest, Path):
        with manifest.open("r", encoding="utf-8") as handle:
            manifest = json.load(handle)
    return hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()


def scan_asset(path: Path, declared_format: str) -> ScanResult:
    declared_format = declared_format.lower().strip()
    errors: list[str] = []
    warnings: list[str] = []

    if declared_format not in ALLOWED_FORMATS:
        errors.append(f"format_not_allowed:{declared_format}")

    suffix = path.suffix.lower()
    if suffix in BLOCKED_EXTENSIONS:
        errors.append(f"blocked_extension:{suffix}")

    expected_format = EXTENSION_FORMATS.get(suffix)
    if expected_format is None:
        errors.append(f"unknown_extension:{suffix or '<none>'}")
    elif expected_format != declared_format:
        errors.append(f"extension_mismatch:{suffix}:{declared_format}")

    if not path.exists():
        errors.append("file_missing")
        return ScanResult(path, declared_format, False, tuple(errors), tuple(warnings))
    if not path.is_file():
        errors.append("not_a_file")
        return ScanResult(path, declared_format, False, tuple(errors), tuple(warnings))

    with path.open("rb") as fh:
        prefix = fh.read(_PREFIX_SIZE)
    if not prefix:
        errors.append("empty_file")
    for magic, name in BLOCKED_MAGIC_BYTES.items():
        if prefix.startswith(magic):
            errors.append(f"blocked_magic:{name}")
            break
    for marker in TORCH_LOAD_MARKERS:
        if marker in prefix:
            errors.append("blocked_torch_marker")
            break

    errors.extend(_format_specific_errors(path, declared_format, prefix))
    return ScanResult(path, declared_format, not errors, tuple(errors), tuple(warnings))


def _format_specific_errors(path: Path, declared_format: str, prefix: bytes) -> list[str]:
    if declared_format == "json":
        return _validate_json(path)
    elif declared_format == "jsonl":
        return _validate_jsonl(path)
    elif declared_format in {"txt", "vocab"}:
        return _validate_utf8(path)
    elif declared_format == "npy" and not prefix.startswith(b"\x93NUMPY"):
        return ["invalid_npy_header"]
    elif declared_format == "npz":
        return _scan_npz(path)
    elif declared_format == "parquet":
        return _validate_parquet(path, prefix)
    elif declared_format == "safetensors":
        return _scan_safetensors_header(path, prefix)
    return []


def _validate_json(path: Path) -> list[str]:
    size = path.stat().st_size
    if size > _JSON_MAX_SIZE:
        return ["json_too_large"]
    try:
        with path.open("r", encoding="utf-8") as fh:
            json.load(fh)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["invalid_json"]
    return []


def _validate_jsonl(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line_number, line in enumerate(fh, start=1):
                if line.strip():
                    json.loads(line)
    except UnicodeDecodeError:
        return ["invalid_utf8"]
    except json.JSONDecodeError as exc:
        return [f"invalid_jsonl:{line_number}:{exc.msg}"]
    return []


def _validate_utf8(path: Path) -> list[str]:
    try:
        with path.open("r", encoding="utf-8") as fh:
            while fh.read(_PREFIX_SIZE):
                pass
    except UnicodeDecodeError:
        return ["invalid_utf8"]
    return []


def _validate_parquet(path: Path, prefix: bytes) -> list[str]:
    if not prefix.startswith(b"PAR1"):
        return ["invalid_parquet_magic"]
    with path.open("rb") as fh:
        fh.seek(-4, 2)
        trailer = fh.read(4)
    if trailer != b"PAR1":
        return ["invalid_parquet_magic"]
    return []


def _scan_npz(path: Path) -> list[str]:
    if not zipfile.is_zipfile(path):
        return ["invalid_npz_zip"]
    errors: list[str] = []
    with zipfile.ZipFile(path) as archive:
        for member in archive.namelist():
            if member.endswith(("/", "\\")):
                continue
            if not member.endswith(".npy"):
                errors.append(f"invalid_npz_member:{member}")
                continue
            with archive.open(member) as handle:
                header = handle.read(6)
            if header != b"\x93NUMPY":
                errors.append(f"invalid_npz_member_header:{member}")
    return errors


def _scan_safetensors_header(path: Path, prefix: bytes) -> list[str]:
    if len(prefix) < 10:
        return ["invalid_safetensors_header"]
    header_len = int.from_bytes(prefix[:8], "little", signed=False)
    if header_len <= 0:
        return ["invalid_safetensors_header"]
    file_size = path.stat().st_size
    if 8 + header_len > file_size:
        return ["invalid_safetensors_header"]
    if header_len <= len(prefix) - 8:
        header_bytes = prefix[8 : 8 + header_len]
    else:
        with path.open("rb") as fh:
            fh.seek(8)
            header_bytes = fh.read(header_len)
    try:
        json.loads(header_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ["invalid_safetensors_header_json"]
    return []
