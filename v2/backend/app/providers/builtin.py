from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
from typing import Any

from ..core.config import CONNECTED_LOCAL_ASSET_ROOT_REF, RUNTIME_ROOT
from .base import ProviderAdapterError, ProviderExecutionRequest


@dataclass(frozen=True)
class MockProviderAdapter:
    adapter_kind: str = "mock"
    display_name: str = "Mock"
    external: bool = False
    execution_enabled: bool = True
    requires_credential: bool = False
    supported_work_kinds: frozenset[str] = frozenset({
        "generate_keyframe",
        "generate_i2v_clip",
        "generate_t2v_clip",
        "generate_tts",
        "assemble_timeline_contract",
    })

    def execute(self, request: ProviderExecutionRequest) -> dict[str, Any]:
        return {
            "schema_version": "mock-provider-response.v1",
            "result": "simulated",
            "request_fingerprint": request.request_fingerprint,
            "media_created": False,
            "provider_task_id": None,
        }


@dataclass(frozen=True)
class LocalTimelineAdapter:
    adapter_kind: str = "local"
    display_name: str = "Local timeline contract"
    external: bool = False
    execution_enabled: bool = True
    requires_credential: bool = False
    supported_work_kinds: frozenset[str] = frozenset({"assemble_timeline_contract"})

    def execute(self, request: ProviderExecutionRequest) -> dict[str, Any]:
        if request.work_kind not in self.supported_work_kinds:
            raise ProviderAdapterError(
                "PROVIDER_ADAPTER_NOT_CONNECTED",
                f"Adapter {self.adapter_kind!r} is not registered for work kind {request.work_kind!r}.",
            )
        return {
            "schema_version": "timeline-contract-result.v1",
            "result": "contract_assembled",
            "input_work_item_ids": list(request.parent_work_item_ids),
            "media_created": False,
        }


def _srt_time(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


@dataclass(frozen=True)
class LocalSubtitleAdapter:
    adapter_kind: str = "local_subtitle"
    display_name: str = "本地确定性字幕"
    external: bool = False
    execution_enabled: bool = True
    requires_credential: bool = False
    supported_work_kinds: frozenset[str] = frozenset({"generate_subtitles"})

    def execute(self, request: ProviderExecutionRequest) -> dict[str, Any]:
        manifest = request.request_manifest
        input_contract = manifest.get("input_contract") if isinstance(manifest.get("input_contract"), dict) else {}
        output_contract = manifest.get("output_contract") if isinstance(manifest.get("output_contract"), dict) else {}
        storage = manifest.get("storage_policy") if isinstance(manifest.get("storage_policy"), dict) else {}
        if output_contract.get("media_type") != "subtitle":
            raise ProviderAdapterError("SUBTITLE_OUTPUT_CONTRACT_INVALID", "字幕步骤必须冻结 subtitle 输出合同。")
        if storage.get("backend_kind") != "local" or storage.get("local_root_ref") != CONNECTED_LOCAL_ASSET_ROOT_REF:
            raise ProviderAdapterError("SUBTITLE_STORAGE_UNSUPPORTED", "字幕生成当前只连接 V2 本地素材库。")
        cues = input_contract.get("cues")
        duration_ms = input_contract.get("duration_ms")
        if not isinstance(cues, list) or not cues or not isinstance(duration_ms, int) or duration_ms <= 0:
            raise ProviderAdapterError("SUBTITLE_CUES_INVALID", "字幕步骤缺少冻结字幕条目或成片时长。")
        lines: list[str] = []
        previous_out = 0
        for index, cue in enumerate(cues, 1):
            if not isinstance(cue, dict):
                raise ProviderAdapterError("SUBTITLE_CUE_INVALID", f"字幕条目 {index} 不是对象。")
            cue_in = cue.get("timeline_in_ms")
            cue_out = cue.get("timeline_out_ms")
            text = str(cue.get("text") or "").strip()
            if (
                not isinstance(cue_in, int)
                or not isinstance(cue_out, int)
                or cue_in < previous_out
                or cue_out <= cue_in
                or cue_out > duration_ms
                or not text
                or "\x00" in text
            ):
                raise ProviderAdapterError("SUBTITLE_CUE_INVALID", f"字幕条目 {index} 的时间或文本合同无效。")
            lines.extend([
                str(index),
                f"{_srt_time(cue_in)} --> {_srt_time(cue_out)}",
                text,
                "",
            ])
            previous_out = cue_out
        content = ("\n".join(lines).rstrip() + "\n").encode("utf-8")
        max_bytes = storage.get("max_file_size_bytes")
        if not isinstance(max_bytes, int) or len(content) > max_bytes:
            raise ProviderAdapterError("SUBTITLE_OUTPUT_TOO_LARGE", "字幕文件超过冻结存储策略的文件上限。")
        relative = Path("assets") / "providers" / "local_subtitle" / request.request_fingerprint / "subtitles.srt"
        output_path = RUNTIME_ROOT / relative
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.exists():
            raise ProviderAdapterError("SUBTITLE_OUTPUT_PATH_EXISTS", "字幕目标路径已存在，不能覆盖。")
        output_path.write_bytes(content)
        return {
            "schema_version": "local-subtitle-response.v1",
            "media_created": True,
            "outputs": [{
                "uri": f"runtime://{relative.as_posix()}",
                "storage_backend": "local",
                "asset_type": "subtitle",
                "role": "voiceover_subtitles",
                "mime_type": "application/x-subrip",
                "content_hash": hashlib.sha256(content).hexdigest(),
                "duration_ms": duration_ms,
                "cue_count": len(cues),
            }],
        }
