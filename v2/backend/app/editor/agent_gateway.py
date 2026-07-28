from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..creation.agent_gateway import AgentChatTransport, AgentGatewayError, HttpxAgentChatTransport
from ..db.models import ModelConfigVersion, ProductionConfigVersion, ProviderConfigVersion


EDITOR_INPUT_CONTRACT_VERSION = "editor-assistant-input.v2"
EDITOR_OUTPUT_SCHEMA_VERSION = "timeline-candidate.v1"
EDITOR_PROMPT_CONTRACT_VERSION = "editor-assistant-prompt.v2"


class EditorTimelineItemOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeline_item_code: str = Field(pattern=r"^ITEM_[0-9]{3}$")
    source_asset_id: str | None = Field(default=None, max_length=48)
    shot_code: str | None = Field(default=None, pattern=r"^SH-[0-9]{3}$")
    source_in_ms: int | None = Field(default=None, ge=0)
    source_out_ms: int | None = Field(default=None, ge=0)
    timeline_in_ms: int = Field(ge=0)
    timeline_out_ms: int = Field(gt=0)
    selection_reason: str = Field(min_length=1, max_length=1000)
    qc_report_ids: list[str] = Field(default_factory=list, max_length=20)
    gap_reason: str | None = Field(default=None, max_length=500)


class EditorAssistantOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_ms: int = Field(gt=0)
    video_items: list[EditorTimelineItemOutput] = Field(min_length=1, max_length=500)
    rhythm_notes: list[str] = Field(default_factory=list, max_length=30)
    subtitle_cues: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    audio_cues: list[dict[str, Any]] = Field(default_factory=list, max_length=200)


@dataclass(frozen=True)
class EditorSelection:
    production_config_version_id: str
    model_config_version_id: str
    provider_config_version_id: str
    model_provider: str
    model_name: str
    provider_model_id: str
    base_url: str
    api_key: str | None
    timeout_seconds: int
    input_contract_version: str
    prompt_contract_version: str
    output_schema_version: str
    max_output_tokens: int | None
    sampling: dict[str, Any]


@dataclass(frozen=True)
class EditorAssistantResult:
    output: EditorAssistantOutput
    raw_output: dict[str, Any]
    provider_request_id: str | None
    token_usage: dict[str, Any]


class EditorAssistantGateway(Protocol):
    def select(self, session: Session, production_config_version_id: str) -> EditorSelection: ...

    def invoke(self, selection: EditorSelection, manifest_payload: dict[str, Any]) -> EditorAssistantResult: ...


_SYSTEM_PROMPT = """你是片场 V2 的剪辑助理。你只根据已确认方案和已批准素材提出时间线候选，不确认时间线、不渲染成片、不生成或替换素材。
只返回符合提供的 JSON Schema 的 JSON 对象。
规则：
1. source_asset_id 只能逐字复制 approved_assets 中的 id；shot_code 和 qc_report_ids 必须逐字复制该素材对应事实。
2. 主视频轨从 0 开始，条目按 timeline_in_ms 连续排列且不得重叠。
3. 可以裁掉素材头尾，但 source_out_ms 不得超过素材真实 duration_ms，时间线时长必须等于源区间时长；不得变速、循环或补帧。
4. 素材不足以覆盖完整成片时，使用 source_asset_id=null 的显式空位，填写 gap_reason 和 selection_reason；不得复用素材、插黑帧或编造资产。
5. 每个真实素材条目必须说明选择理由并携带该素材允许的 QC 报告 ID；不得声称素材已再次审核。
6. audio_policy.mode=off 时 audio_cues 必须为空；subtitle_policy.enabled=false 时 subtitle_cues 必须为空。
7. duration_ms 必须等于 delivery_contract.duration_ms；不得输出 Provider、工作流、费用、任务、Markdown 或 JSON 之外的文字。
8. production_profile 只用于保留素材生产依据。不得改变、重新选择或根据它编造生成任务；时间线条目仍只能消费已批准素材。"""


class ConfiguredEditorAssistantGateway:
    def __init__(self, *, transport: AgentChatTransport | None = None) -> None:
        self.transport = transport or HttpxAgentChatTransport()

    def select(self, session: Session, production_config_version_id: str) -> EditorSelection:
        rows = list(session.execute(
            select(ModelConfigVersion, ProviderConfigVersion, ProductionConfigVersion)
            .join(ProviderConfigVersion, ProviderConfigVersion.id == ModelConfigVersion.provider_config_version_id)
            .join(ProductionConfigVersion, ProductionConfigVersion.id == ModelConfigVersion.production_config_version_id)
            .where(
                ProductionConfigVersion.id == production_config_version_id,
                ProductionConfigVersion.status == "published",
                ModelConfigVersion.agent_role == "editor",
                ModelConfigVersion.status == "published",
                ProviderConfigVersion.status == "published",
            )
            .order_by(ModelConfigVersion.config_key)
        ))
        if not rows:
            raise AgentGatewayError("EDITOR_MODEL_NOT_CONFIGURED", "当前制作配置没有已发布的剪辑助理模型。")
        if len(rows) != 1:
            raise AgentGatewayError("EDITOR_MODEL_SELECTION_AMBIGUOUS", "当前制作配置存在多个剪辑助理模型。")
        model, provider, config = rows[0]
        if provider.adapter_kind != "openai_compatible" or "text_generation" not in (provider.capabilities or []):
            raise AgentGatewayError("EDITOR_MODEL_CAPABILITY_MISSING", "剪辑助理必须使用声明文本生成能力的 OpenAI-compatible 服务。")
        expected = (EDITOR_INPUT_CONTRACT_VERSION, EDITOR_OUTPUT_SCHEMA_VERSION, EDITOR_PROMPT_CONTRACT_VERSION)
        if (model.input_contract_version, model.output_schema_version, model.prompt_contract_version) != expected:
            raise AgentGatewayError("EDITOR_CONTRACT_VERSION_UNSUPPORTED", "剪辑助理模型配置与当前合同版本不一致。")
        return EditorSelection(
            config.id, model.id, provider.id, provider.display_name, model.display_name,
            model.provider_model_id, provider.base_url, provider.api_key,
            provider.request_timeout_seconds, model.input_contract_version,
            model.prompt_contract_version, model.output_schema_version,
            model.max_output_tokens, dict(model.sampling or {}),
        )

    def invoke(self, selection: EditorSelection, manifest_payload: dict[str, Any]) -> EditorAssistantResult:
        if os.getenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
            raise AgentGatewayError("AGENT_MODEL_EXECUTION_DISABLED", "剪辑助理真实调用尚未获得后端执行授权。")
        api_key = str(selection.api_key or "").strip()
        if not api_key:
            raise AgentGatewayError("AGENT_MODEL_CREDENTIAL_UNAVAILABLE", "剪辑助理模型供应商的 API Key 未填写。")
        payload: dict[str, Any] = {
            "model": selection.provider_model_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT + "\n权威输出 Schema：" + json.dumps(EditorAssistantOutput.model_json_schema(), ensure_ascii=False, separators=(",", ":"))},
                {"role": "user", "content": "以下是本次不可变剪辑输入合同：" + json.dumps(manifest_payload, ensure_ascii=False, separators=(",", ":"))},
            ],
            "response_format": {"type": "json_object"},
        }
        if selection.max_output_tokens is not None:
            payload["max_tokens"] = selection.max_output_tokens
        for key, value in selection.sampling.items():
            if key in {"temperature", "top_p", "frequency_penalty", "presence_penalty", "seed"}:
                payload[key] = value
        response = self.transport.create_chat_completion(
            url=urljoin(selection.base_url.rstrip("/") + "/", "chat/completions"),
            api_key=api_key,
            payload=payload,
            timeout_seconds=selection.timeout_seconds,
        )
        parsed: Any = None
        try:
            parsed = json.loads(response["choices"][0]["message"]["content"])
            output = EditorAssistantOutput.model_validate(parsed)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AgentGatewayError("EDITOR_OUTPUT_SCHEMA_INVALID", "剪辑助理输出不是有效 JSON 对象。") from exc
        except ValidationError as exc:
            raise AgentGatewayError(
                "EDITOR_OUTPUT_SCHEMA_INVALID",
                "剪辑助理输出不符合严格时间线候选合同。",
                raw_output=parsed if isinstance(parsed, dict) else None,
                diagnostics=exc.errors(include_input=False),
            ) from exc
        return EditorAssistantResult(
            output, parsed, str(response.get("id") or "").strip() or None,
            response.get("usage") if isinstance(response.get("usage"), dict) else {},
        )


class DeterministicEditorAssistantGateway:
    """Explicit test gateway; never registered by the runtime application."""

    def select(self, session: Session, production_config_version_id: str) -> EditorSelection:
        return EditorSelection(
            production_config_version_id, "model_config_test_editor", "provider_config_test_mock",
            "mock", "deterministic-editor-v1", "deterministic-editor-v1",
            "https://example.invalid/v1", None, 1,
            EDITOR_INPUT_CONTRACT_VERSION, EDITOR_PROMPT_CONTRACT_VERSION,
            EDITOR_OUTPUT_SCHEMA_VERSION, None, {},
        )

    def invoke(self, selection: EditorSelection, manifest_payload: dict[str, Any]) -> EditorAssistantResult:
        cursor = 0
        items = []
        for index, asset in enumerate(manifest_payload["approved_assets"], 1):
            if asset["asset_type"] != "video" or not asset["duration_ms"] or cursor >= manifest_payload["delivery_contract"]["duration_ms"]:
                continue
            duration = min(asset["duration_ms"], manifest_payload["delivery_contract"]["duration_ms"] - cursor)
            items.append(EditorTimelineItemOutput(
                timeline_item_code=f"ITEM_{len(items) + 1:03d}",
                source_asset_id=asset["id"],
                shot_code=asset["shot_code"],
                source_in_ms=0,
                source_out_ms=duration,
                timeline_in_ms=cursor,
                timeline_out_ms=cursor + duration,
                selection_reason="素材已通过人工审核，并按分镜顺序用于主画面。",
                qc_report_ids=asset["qc_report_ids"],
            ))
            cursor += duration
        target = manifest_payload["delivery_contract"]["duration_ms"]
        if cursor < target:
            items.append(EditorTimelineItemOutput(
                timeline_item_code=f"ITEM_{len(items) + 1:03d}",
                timeline_in_ms=cursor,
                timeline_out_ms=target,
                selection_reason="现有已批准视频不足以覆盖完整成片。",
                gap_reason="缺少可覆盖剩余时长的已批准视频素材。",
            ))
        output = EditorAssistantOutput(duration_ms=target, video_items=items)
        raw = output.model_dump(mode="json")
        return EditorAssistantResult(output, raw, "deterministic-editor", {"total_tokens": 1})


def get_editor_assistant_gateway() -> EditorAssistantGateway:
    return ConfiguredEditorAssistantGateway()
