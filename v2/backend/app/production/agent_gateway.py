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
from ..providers.credentials import EnvironmentCredentialResolver


PRODUCTION_PLANNER_INPUT_CONTRACT_VERSION = "production-planner-input.v1"
PRODUCTION_PLANNER_OUTPUT_SCHEMA_VERSION = "production-plan-candidate.v1"
PRODUCTION_PLANNER_PROMPT_CONTRACT_VERSION = "production-planner-prompt.v2"


class ProductionRouteAssignmentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_code: str = Field(pattern=r"^SH-[0-9]{3}$")
    keyframe_workflow_slot_version_id: str | None = Field(default=None, max_length=48)
    video_workflow_slot_version_id: str = Field(min_length=1, max_length=48)
    required_input_sources: list[str] = Field(min_length=1, max_length=40)
    reason: str = Field(min_length=1, max_length=1000)


class ProductionPlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assignments: list[ProductionRouteAssignmentOutput] = Field(min_length=1, max_length=200)


@dataclass(frozen=True)
class ProductionPlannerSelection:
    production_config_version_id: str
    model_config_version_id: str
    provider_config_version_id: str
    model_provider: str
    model_name: str
    provider_model_id: str
    base_url: str
    credential_ref: str | None
    timeout_seconds: int
    input_contract_version: str
    prompt_contract_version: str
    output_schema_version: str
    max_output_tokens: int | None
    sampling: dict[str, Any]


@dataclass(frozen=True)
class ProductionPlannerResult:
    output: ProductionPlannerOutput
    raw_output: dict[str, Any]
    provider_request_id: str | None
    token_usage: dict[str, Any]


class ProductionPlannerGateway(Protocol):
    def select(self, session: Session, production_config_version_id: str) -> ProductionPlannerSelection: ...

    def invoke(self, selection: ProductionPlannerSelection, manifest_payload: dict[str, Any]) -> ProductionPlannerResult: ...


_SYSTEM_PROMPT = """你是片场 V2 的制作规划智能体。你只根据已确认分镜和用户选定的制作配置，为每个镜头提出图片与视频工作流候选；不创建任务、不执行工作流、不切换配置、不决定费用。
只返回严格 JSON：{"assignments":[{"shot_code":"SH-001","keyframe_workflow_slot_version_id":"精确槽位ID或null","video_workflow_slot_version_id":"精确槽位ID","required_input_sources":["逐字复制的必需输入来源"],"reason":"普通用户可读的选择理由"}]}。
规则：
1. 必须为输入中的每个镜头返回且只返回一项，顺序和 shot_code 完全一致。
2. 工作流 ID 只能逐字复制 available_workflow_slots 中的 id，不得使用显示名称、工作流供应商 ID、缩写或自造 ID。
3. operation_kind=image_generation 的槽位只能作为关键帧方案；video_generation、multi_frame_video_generation 或 text_to_video_generation 只能作为视频方案。
4. video_generation 和 multi_frame_video_generation 必须同时选择一个关键帧方案；text_to_video_generation 必须把关键帧方案设为 null。
5. 所选槽位必须显式支持 selected_video_spec.id。
6. required_input_sources 必须逐字包含两个所选槽位中 required=true 的全部输入来源；不得遗漏、增加、改名或重复。数组排列顺序没有业务含义。
7. generation_requirements.reference_image_required 或 identity_consistency_required 为 true 时，关键帧槽位的 input_sources 必须包含 reference_image.primary，并且镜头必须已有 primary_reference_entity_version_id。
8. multi_frame_required 为 true 时，视频槽位必须声明 multi_frame；precise_text_required 为 true 时，关键帧槽位必须声明 precise_text。
9. reason 只解释已声明的镜头要求和工作流能力如何匹配，不得声称已经生成、通过审核或开始扣费。
10. 找不到满足全部要求的路线时不要猜测或退化。仍须使用严格 JSON，但未知、替代或不兼容 ID 会被后端明确拒绝并保留失败证据。
11. 不得输出 Provider、密钥、NodeInfoList、价格、任务、重试建议、Markdown 或 JSON 之外的文字。"""


class ConfiguredProductionPlannerGateway:
    def __init__(self, *, transport: AgentChatTransport | None = None, credential_resolver: EnvironmentCredentialResolver | None = None) -> None:
        self.transport = transport or HttpxAgentChatTransport()
        self.credential_resolver = credential_resolver or EnvironmentCredentialResolver.from_environment()

    def select(self, session: Session, production_config_version_id: str) -> ProductionPlannerSelection:
        rows = list(session.execute(
            select(ModelConfigVersion, ProviderConfigVersion, ProductionConfigVersion)
            .join(ProviderConfigVersion, ProviderConfigVersion.id == ModelConfigVersion.provider_config_version_id)
            .join(ProductionConfigVersion, ProductionConfigVersion.id == ModelConfigVersion.production_config_version_id)
            .where(
                ProductionConfigVersion.id == production_config_version_id,
                ProductionConfigVersion.status == "published",
                ModelConfigVersion.agent_role == "production_planner",
                ModelConfigVersion.status == "published",
                ProviderConfigVersion.status == "published",
            )
            .order_by(ModelConfigVersion.config_key)
        ))
        if not rows:
            raise AgentGatewayError("PRODUCTION_PLANNER_MODEL_NOT_CONFIGURED", "所选制作配置没有已发布的制作规划模型。")
        if len(rows) != 1:
            raise AgentGatewayError("PRODUCTION_PLANNER_MODEL_SELECTION_AMBIGUOUS", "所选制作配置存在多个制作规划模型，请保留一个明确选择。")
        model, provider, config = rows[0]
        if provider.adapter_kind != "openai_compatible":
            raise AgentGatewayError("PRODUCTION_PLANNER_ADAPTER_UNSUPPORTED", "制作规划模型没有绑定 OpenAI-compatible 服务供应商。")
        if "text_generation" not in (provider.capabilities or []):
            raise AgentGatewayError("PRODUCTION_PLANNER_CAPABILITY_MISSING", "制作规划模型供应商未声明文本生成能力。")
        expected = (
            PRODUCTION_PLANNER_INPUT_CONTRACT_VERSION,
            PRODUCTION_PLANNER_OUTPUT_SCHEMA_VERSION,
            PRODUCTION_PLANNER_PROMPT_CONTRACT_VERSION,
        )
        actual = (model.input_contract_version, model.output_schema_version, model.prompt_contract_version)
        if actual != expected:
            raise AgentGatewayError("PRODUCTION_PLANNER_CONTRACT_VERSION_UNSUPPORTED", "制作规划模型配置的合同版本与当前代码不一致。")
        return ProductionPlannerSelection(
            config.id, model.id, provider.id, provider.display_name, model.display_name,
            model.provider_model_id, provider.base_url, provider.credential_ref,
            provider.request_timeout_seconds, model.input_contract_version,
            model.prompt_contract_version, model.output_schema_version,
            model.max_output_tokens, dict(model.sampling or {}),
        )

    def invoke(self, selection: ProductionPlannerSelection, manifest_payload: dict[str, Any]) -> ProductionPlannerResult:
        if os.getenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
            raise AgentGatewayError("AGENT_MODEL_EXECUTION_DISABLED", "制作规划模型真实调用尚未获得后端执行授权。")
        credential = self.credential_resolver.resolve(selection.credential_ref)
        if not credential.available or credential.secret is None:
            raise AgentGatewayError("AGENT_MODEL_CREDENTIAL_UNAVAILABLE", f"制作规划模型后端凭据不可用（{credential.state}）。")
        payload: dict[str, Any] = {
            "model": selection.provider_model_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": "以下是本次不可变制作规划输入合同：" + json.dumps(manifest_payload, ensure_ascii=False, separators=(",", ":"))},
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
            api_key=credential.secret,
            payload=payload,
            timeout_seconds=selection.timeout_seconds,
        )
        try:
            parsed = json.loads(response["choices"][0]["message"]["content"])
            output = ProductionPlannerOutput.model_validate(parsed)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AgentGatewayError("PRODUCTION_PLANNER_OUTPUT_SCHEMA_INVALID", "制作规划智能体输出不是有效 JSON 对象。") from exc
        except ValidationError as exc:
            raise AgentGatewayError(
                "PRODUCTION_PLANNER_OUTPUT_SCHEMA_INVALID",
                "制作规划智能体输出不符合严格候选合同。",
                raw_output=parsed if isinstance(parsed, dict) else None,
                diagnostics=exc.errors(include_input=False),
            ) from exc
        return ProductionPlannerResult(
            output,
            parsed,
            str(response.get("id") or "").strip() or None,
            response.get("usage") if isinstance(response.get("usage"), dict) else {},
        )


class DeterministicProductionPlannerGateway:
    """Explicit test gateway; never registered by the runtime application."""

    def select(self, session: Session, production_config_version_id: str) -> ProductionPlannerSelection:
        return ProductionPlannerSelection(
            production_config_version_id, "model_config_test_production_planner", "provider_config_test_mock",
            "mock", "deterministic-production-planner-v1", "deterministic-production-planner-v1",
            "https://example.invalid/v1", None, 1,
            PRODUCTION_PLANNER_INPUT_CONTRACT_VERSION, PRODUCTION_PLANNER_PROMPT_CONTRACT_VERSION,
            PRODUCTION_PLANNER_OUTPUT_SCHEMA_VERSION, None, {},
        )

    def invoke(self, selection: ProductionPlannerSelection, manifest_payload: dict[str, Any]) -> ProductionPlannerResult:
        slots = manifest_payload["available_workflow_slots"]
        image_slots = [item for item in slots if item["operation_kind"] == "image_generation"]
        video_slots = [item for item in slots if item["operation_kind"] in {"video_generation", "multi_frame_video_generation", "text_to_video_generation"}]
        assignments = []
        for shot in manifest_payload["shots"]:
            requirements = shot["generation_requirements"]
            keyframe = next((item for item in image_slots if (
                (not requirements["reference_image_required"] or "reference_image.primary" in item["input_sources"])
                and (not requirements["precise_text_required"] or "precise_text" in item["capability_tags"])
            )), image_slots[0] if image_slots else None)
            video = next((item for item in video_slots if (
                (item["operation_kind"] != "video_generation" or keyframe is not None)
                and (not requirements["multi_frame_required"] or "multi_frame" in item["capability_tags"])
            )), video_slots[0])
            if video["operation_kind"] == "text_to_video_generation":
                keyframe = None
            sources = sorted(set(
                (keyframe["required_input_sources"] if keyframe else []) + video["required_input_sources"]
            ))
            assignments.append(ProductionRouteAssignmentOutput(
                shot_code=shot["shot_code"],
                keyframe_workflow_slot_version_id=keyframe["id"] if keyframe else None,
                video_workflow_slot_version_id=video["id"],
                required_input_sources=sources,
                reason="所选方案与镜头声明的参考图、连续性和运动要求匹配。",
            ))
        output = ProductionPlannerOutput(assignments=assignments)
        raw = output.model_dump(mode="json")
        return ProductionPlannerResult(output, raw, "deterministic-production-planner", {"total_tokens": 1})


def get_production_planner_gateway() -> ProductionPlannerGateway:
    return ConfiguredProductionPlannerGateway()
