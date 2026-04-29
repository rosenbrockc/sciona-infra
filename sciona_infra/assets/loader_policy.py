from __future__ import annotations

from dataclasses import dataclass

from sciona_infra.assets.format_scanner import ALLOWED_FORMATS


@dataclass(frozen=True)
class LoaderPolicy:
    format: str
    loader: str
    constraints: tuple[str, ...]
    mmap_safe: bool = False


LOADER_POLICIES: dict[str, LoaderPolicy] = {
    "safetensors": LoaderPolicy(
        "safetensors",
        "safetensors.safe_open",
        ("no_torch_pickle_fallback",),
        mmap_safe=True,
    ),
    "onnx": LoaderPolicy(
        "onnx",
        "onnxruntime.InferenceSession",
        ("custom_ops_disabled_by_default", "no_external_initializer_without_hash"),
    ),
    "json": LoaderPolicy(
        "json",
        "json.load",
        ("schema_validation_against_declared_metadata",),
    ),
    "jsonl": LoaderPolicy(
        "jsonl",
        "json.loads",
        ("line_delimited_json", "schema_validation_against_declared_metadata"),
    ),
    "parquet": LoaderPolicy(
        "parquet",
        "pyarrow.parquet.read_table",
        ("explicit_schema_check",),
        mmap_safe=True,
    ),
    "npy": LoaderPolicy(
        "npy",
        "numpy.load",
        ("allow_pickle_false", "dtype_allowlist"),
        mmap_safe=True,
    ),
    "npz": LoaderPolicy(
        "npz",
        "numpy.load",
        ("allow_pickle_false", "dtype_allowlist"),
    ),
    "txt": LoaderPolicy(
        "txt",
        "pathlib.Path.read_text",
        ("utf8_only", "normalized_line_endings"),
    ),
    "vocab": LoaderPolicy(
        "vocab",
        "pathlib.Path.read_text",
        ("utf8_only", "normalized_line_endings"),
    ),
}


def get_loader_policy(format_name: str) -> LoaderPolicy:
    normalized = format_name.lower().strip()
    try:
        return LOADER_POLICIES[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported asset format: {format_name}") from exc


def validate_loader_policy(format_name: str, loader_name: str = "") -> tuple[bool, tuple[str, ...]]:
    normalized = format_name.lower().strip()
    if normalized not in ALLOWED_FORMATS:
        return False, (f"format_not_allowed:{format_name}",)
    policy = get_loader_policy(normalized)
    if loader_name and loader_name != policy.loader:
        return False, (f"loader_mismatch:{loader_name}:{policy.loader}",)
    return True, ()
