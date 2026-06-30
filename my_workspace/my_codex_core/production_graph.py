from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any


GRAPH_SCHEMA_VERSION = 1
SEMANTIC_INPUT_ALIASES = {
    "input_base_image": ("input_base_image", "reference_image"),
    "input_middle_frame": ("input_middle_frame", "middle_frame_image"),
    "input_last_frame": ("input_last_frame", "last_frame_image"),
    "input_mask_image": ("input_mask_image", "mask_image"),
    "input_reference_style": ("input_reference_style", "reference_style"),
    "input_audio_file": ("input_audio_file", "audio_file"),
}
SEMANTIC_OUTPUTS = ("output_final_image", "output_final_video", "output_mask_alpha")


def normalize_global_context(payload: dict[str, Any], video_config: dict[str, Any] | None = None) -> dict[str, Any]:
    source = payload.get("global_context") if isinstance(payload.get("global_context"), dict) else {}
    video = video_config or {}
    render = source.get("render") if isinstance(source.get("render"), dict) else {}
    width = _positive_int(render.get("working_width") or payload.get("width") or video.get("width"), 848)
    height = _positive_int(render.get("working_height") or payload.get("height") or video.get("height"), 480)
    context = {
        "characters": source.get("characters") if isinstance(source.get("characters"), list) else [],
        "style": source.get("style") if isinstance(source.get("style"), dict) else {},
        "render": {
            "working_width": width,
            "working_height": height,
            "delivery_width": _positive_int(render.get("delivery_width"), 1920),
            "delivery_height": _positive_int(render.get("delivery_height"), 1080),
            "frame_rate": _positive_int(render.get("frame_rate") or payload.get("fps") or video.get("fps"), 24),
            "aspect_ratio": str(render.get("aspect_ratio") or video.get("aspect_ratio") or "16:9"),
        },
    }
    style = context["style"]
    style.setdefault("style_id", str(payload.get("style_id") or ""))
    style.setdefault("reference_asset", str(payload.get("input_reference_style") or payload.get("reference_style") or ""))
    style.setdefault("weight", payload.get("global_style_weight") or payload.get("style_weight") or "")
    return context


def build_production_graph(
    task_id: str,
    jobs: list[dict[str, Any]],
    global_context: dict[str, Any],
    packaging_jobs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    graph_jobs = []
    seen: set[str] = set()
    for index, source in enumerate(jobs, 1):
        job_id = _unique_job_id(source.get("job_id") or source.get("name") or f"material_{index:03d}", seen)
        seen.add(job_id)
        job_type = str(source.get("type") or "image").strip().lower()
        outputs = source.get("outputs") if isinstance(source.get("outputs"), list) else []
        if not outputs:
            outputs = ["output_final_video" if job_type == "video" else "output_final_image"]
            if str(source.get("mode") or "") == "background_remove":
                outputs.append("output_mask_alpha")
        graph_jobs.append(
            {
                "job_id": job_id,
                "stage": "visual",
                "capability": str(source.get("capability") or ("video_generate" if job_type == "video" else "image_generate")),
                "mode": str(source.get("mode") or source.get("workflow_mode") or ""),
                "workflow_id": str(source.get("workflow_id") or ""),
                "depends_on": _string_list(source.get("depends_on")),
                "inputs": source.get("input_bindings") if isinstance(source.get("input_bindings"), dict) else {},
                "params": source.get("params") if isinstance(source.get("params"), dict) else {},
                "outputs": outputs,
                "resource_class": "video" if job_type == "video" else "image",
                "retry": {"max_attempts": 3, "retry_on": ["network", "timeout", "provider_busy", "download"]},
            }
        )
    for source in packaging_jobs or []:
        job_id = _unique_job_id(source.get("job_id") or "packaging", seen)
        seen.add(job_id)
        graph_jobs.append(
            {
                "job_id": job_id,
                "stage": "08_audio_visual_packaging",
                "capability": str(source.get("capability") or "audio_visual_packaging"),
                "mode": str(source.get("mode") or job_id),
                "workflow_id": "",
                "depends_on": _string_list(source.get("depends_on")),
                "inputs": source.get("inputs") if isinstance(source.get("inputs"), dict) else {},
                "params": source.get("params") if isinstance(source.get("params"), dict) else {},
                "outputs": source.get("outputs") if isinstance(source.get("outputs"), list) else [],
                "resource_class": str(source.get("resource_class") or "local"),
                "retry": source.get("retry") if isinstance(source.get("retry"), dict) else {"max_attempts": 1, "retry_on": []},
            }
        )
    return {
        "schema_version": GRAPH_SCHEMA_VERSION,
        "task_id": str(task_id),
        "execution": "sequential_dag",
        "global_context": global_context,
        "jobs": graph_jobs,
        "created_at": time.time(),
    }


def topological_job_ids(graph: dict[str, Any]) -> list[str]:
    jobs = graph.get("jobs") if isinstance(graph.get("jobs"), list) else []
    by_id = {str(job.get("job_id")): job for job in jobs if isinstance(job, dict) and job.get("job_id")}
    order: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(job_id: str) -> None:
        if job_id in visited:
            return
        if job_id in visiting:
            raise ValueError(f"production graph contains a dependency cycle at {job_id}")
        visiting.add(job_id)
        for dependency in _string_list(by_id[job_id].get("depends_on")):
            if dependency not in by_id:
                raise ValueError(f"production graph dependency does not exist: {job_id} -> {dependency}")
            visit(dependency)
        visiting.remove(job_id)
        visited.add(job_id)
        order.append(job_id)

    for item_id in by_id:
        visit(item_id)
    return order


def stable_job_hash(job: dict[str, Any], workflow_config: dict[str, Any], resolved_inputs: dict[str, Any]) -> str:
    normalized_inputs: dict[str, Any] = {}
    for key, value in resolved_inputs.items():
        path = Path(str(value))
        if path.is_file():
            digest = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
            normalized_inputs[key] = {"path": str(path), "size": path.stat().st_size, "sha256": digest.hexdigest()}
        else:
            normalized_inputs[key] = value
    value = {
        "job": job,
        "workflow": {
            "endpoint": workflow_config.get("workflow_endpoint") or workflow_config.get("endpoint") or "",
            "node_info_list_json": workflow_config.get("node_info_list_json") or "[]",
            "workflow_preset_id": workflow_config.get("workflow_preset_id") or "",
        },
        "resolved_inputs": normalized_inputs,
    }
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def artifact_record(path: str | Path, task_id: str, producer_job_id: str, output_name: str, business_asset_id: str = "") -> dict[str, Any]:
    target = Path(path)
    checksum = ""
    if target.is_file():
        digest = hashlib.sha256()
        with target.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        checksum = digest.hexdigest()
    return {
        "task_id": task_id,
        "producer_job_id": producer_job_id,
        "output_name": output_name,
        "business_asset_id": business_asset_id,
        "path": str(target),
        "media_type": target.suffix.lower().lstrip("."),
        "sha256": checksum,
        "size": target.stat().st_size if target.is_file() else 0,
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _positive_int(value: Any, default: int) -> int:
    try:
        number = int(float(str(value)))
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [part.strip() for part in value.split(",") if part.strip()]
    if isinstance(value, list):
        return [str(part).strip() for part in value if str(part).strip()]
    return []


def _unique_job_id(value: Any, seen: set[str]) -> str:
    base = "".join(char if char.isalnum() or char in {"_", "-"} else "_" for char in str(value or "job").strip()).strip("_") or "job"
    candidate = base
    index = 2
    while candidate in seen:
        candidate = f"{base}_{index}"
        index += 1
    return candidate
