#!/usr/bin/env python3
"""Build the exact static artifact published to GitHub Pages.

Only browser assets and validated public JSON are copied. Internal checkpoints, source
code, tests, workflow files, and secrets never enter the Pages artifact.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any


def _load_json_object(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _copy_required(source: Path, destination: Path) -> None:
    if not source.exists() or not source.is_file():
        raise FileNotFoundError(f"Required site file is missing: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)


def build_site(root: Path, output: Path) -> list[str]:
    warnings: list[str] = []
    current_path = root / "data" / "current.json"
    current = _load_json_object(current_path)
    if not isinstance(current.get("tokens"), dict):
        raise ValueError("data/current.json has no valid `tokens` object")

    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)

    _copy_required(root / "index.html", output / "index.html")
    if (root / "assets").is_dir():
        shutil.copytree(root / "assets", output / "assets")
    else:
        raise FileNotFoundError("Required assets directory is missing")

    (output / ".nojekyll").write_text("", encoding="utf-8")
    data_output = output / "data"
    history_output = data_output / "history"
    history_output.mkdir(parents=True)
    shutil.copy2(current_path, data_output / "current.json")

    history_dir = root / "data" / "history"
    for path in sorted(history_dir.glob("*.json")) if history_dir.exists() else []:
        try:
            history = _load_json_object(path)
            snapshots = history.get("snapshots")
            if not isinstance(snapshots, list):
                raise ValueError("missing snapshots array")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            warnings.append(f"skipped malformed history {path.name}: {exc}")
            continue
        shutil.copy2(path, history_output / path.name)

    return warnings


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent)
    parser.add_argument("--output", type=Path, default=Path("_site"))
    args = parser.parse_args()

    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    warnings = build_site(root, output.resolve())
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"built {output.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
