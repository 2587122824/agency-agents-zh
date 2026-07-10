from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any


_JSON_BLOCK = re.compile(r"```json\s*(\{.*?\})\s*```", re.IGNORECASE | re.DOTALL)


def snapshot_linked_assets(task_dir: Path, user_input: str) -> str:
    """Freeze linked task assets before staff and production stages can consume them."""

    match = _JSON_BLOCK.search(user_input or "")
    if not match:
        return user_input
    try:
        payload = json.loads(match.group(1))
    except Exception:
        return user_input
    linked = payload.get("linked_assets") if isinstance(payload, dict) else None
    if not isinstance(linked, dict):
        return user_input

    root = task_dir / "linked_reference_assets"
    root.mkdir(parents=True, exist_ok=True)
    entries: list[dict[str, Any]] = []
    for index, raw in enumerate(linked.get("assets") or [], 1):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        item["selection_rank"] = int(item.get("selection_rank") or index)
        source = _resolve_source_path(str(item.get("file") or ""))
        if source is None:
            item["snapshot_error"] = "linked_asset_not_found"
            entries.append({"asset_id": str(item.get("asset_id") or ""), "error": item["snapshot_error"]})
            raw.update(item)
            continue
        digest = _sha256(source)
        target = root / f"{item.get('selection_rank', index):03d}_{digest[:16]}{source.suffix.lower()}"
        if not target.exists():
            shutil.copy2(source, target)
        item["source_file"] = str(source)
        item["file"] = str(target)
        item["snapshot_file"] = str(target)
        item["source_sha256"] = digest
        item["snapshot_sha256"] = _sha256(target)
        raw.update(item)
        entries.append(
            {
                "asset_id": str(item.get("asset_id") or item.get("id") or ""),
                "selection_rank": item["selection_rank"],
                "source_file": str(source),
                "snapshot_file": str(target),
                "sha256": item["snapshot_sha256"],
            }
        )

    snapshot = {"schema_version": 1, "assets": entries}
    (task_dir / "linked_reference_snapshot.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    return user_input[: match.start(1)] + serialized + user_input[match.end(1) :]


def _resolve_source_path(value: str) -> Path | None:
    text = value.strip().replace("\\", "/")
    if not text:
        return None
    candidates = [Path(text)]
    if not Path(text).is_absolute():
        candidates.append(Path.cwd() / text)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
