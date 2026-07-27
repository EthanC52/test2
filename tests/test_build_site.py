from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from build_site import build_site  # noqa: E402


def test_pages_artifact_excludes_internal_state_and_skips_bad_history(tmp_path: Path) -> None:
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("", encoding="utf-8")
    (tmp_path / "index.html").write_text("ok", encoding="utf-8")
    (tmp_path / "data" / "history").mkdir(parents=True)
    (tmp_path / "data" / "current.json").write_text(
        json.dumps({"tokens": {"eurcv": {}}}), encoding="utf-8"
    )
    (tmp_path / "data" / "history" / "eurcv.json").write_text(
        json.dumps({"snapshots": [{"date": "2026-01-01", "supply": "1"}]}),
        encoding="utf-8",
    )
    (tmp_path / "data" / "history" / "broken.json").write_text("bad", encoding="utf-8")
    (tmp_path / ".state" / "etherscan").mkdir(parents=True)
    (tmp_path / ".state" / "etherscan" / "secret.json").write_text("{}", encoding="utf-8")

    output = tmp_path / "_site"
    warnings = build_site(tmp_path, output)

    assert (output / "index.html").exists()
    assert (output / ".nojekyll").exists()
    assert (output / "data" / "history" / "eurcv.json").exists()
    assert not (output / "data" / "history" / "broken.json").exists()
    assert not (output / ".state").exists()
    assert warnings and "broken.json" in warnings[0]
