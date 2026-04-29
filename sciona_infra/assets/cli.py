from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from sciona_infra.assets.hydrator import asset_ref_from_document, hydrate_cdg, hydrate_refs, write_lockfile


def hydrate(
    cdg_fqdn: str,
    *,
    supabase: Any | None = None,
    cache_dir: Path | None = None,
    strict: bool = False,
    document_json: Path | None = None,
) -> Path:
    cache_dir = cache_dir or Path.home() / ".sciona" / "assets"
    if document_json is not None:
        document = json.loads(document_json.read_text(encoding="utf-8"))
        ref = asset_ref_from_document(document)
        refs = () if ref is None else (ref,)
        dependencies = hydrate_refs(cdg_fqdn, refs, cache_dir=cache_dir, strict=strict)
        return write_lockfile(cdg_fqdn, dependencies, cache_dir=cache_dir)
    if supabase is None:
        raise ValueError("supabase client or document_json is required")
    receipt = asyncio.run(
        hydrate_cdg(cdg_fqdn, supabase=supabase, cache_dir=cache_dir, strict=strict)
    )
    return receipt.lockfile_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sciona hydrate")
    parser.add_argument("cdg_fqdn")
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--cache-dir", type=Path, default=Path.home() / ".sciona" / "assets")
    parser.add_argument(
        "--document-json",
        type=Path,
        help="Local artifact document JSON for offline/scripted hydration.",
    )
    args = parser.parse_args(argv)
    lockfile = hydrate(
        args.cdg_fqdn,
        cache_dir=args.cache_dir,
        strict=args.strict,
        document_json=args.document_json,
    )
    print(lockfile)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
