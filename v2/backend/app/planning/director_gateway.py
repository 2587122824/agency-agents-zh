from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal, Protocol
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..creation.agent_gateway import AgentChatTransport, AgentGatewayError, HttpxAgentChatTransport
from ..db.models import ModelConfigVersion, ProductionConfigVersion, ProviderConfigVersion
from ..providers.credentials import EnvironmentCredentialResolver


class DirectorShotOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shot_code: str = Field(pattern=r"^SH-[0-9]{3}$")
    sequence_number: int = Field(ge=1, le=999)
    duration_ms: int = Field(ge=100, le=3_600_000)
    narrative_beat_code: str = Field(pattern=r"^BEAT_[0-9]{2,3}$")
    continuity_group_id: str | None = Field(default=None, pattern=r"^CONT-[0-9]{3}$")
    action_count: Literal[1]
    shot_type: str = Field(min_length=1, max_length=40)
    scene_entity_version_id: str | None = Field(default=None, max_length=48)
    character_entity_version_ids: list[str] = Field(default_factory=list, max_length=20)
    outfit_entity_version_ids: list[str] = Field(default_factory=list, max_length=20)
    product_entity_version_ids: list[str] = Field(default_factory=list, max_length=20)
    primary_reference_entity_version_id: str | None = Field(default=None, max_length=48)
    face_visibility: Literal["required", "optional", "not_visible"]
    text_policy: Literal["forbidden", "allowed", "required"]
    motion_requirement: Literal["static", "moderate", "significant"]
    audio_requirement: Literal["off", "lip_motion_only", "configured"]
    composition: str = Field(min_length=1, max_length=500)
    action: str = Field(min_length=1, max_length=1000)
    visual_prompt: str = Field(min_length=1, max_length=4000)
    negative_prompt: str | None = Field(default=None, min_length=1, max_length=2000)

    @model_validator(mode="after")
    def references_are_unique(self):
        for field in (
            "character_entity_version_ids",
            "outfit_entity_version_ids",
            "product_entity_version_ids",
        ):
            values = getattr(self, field)
            if len(values) != len(set(values)):
                raise ValueError(f"{field} must contain unique IDs")
        declared = {
            *(self.character_entity_version_ids or []),
            *(self.outfit_entity_version_ids or []),
            *(self.product_entity_version_ids or []),
        }
        if self.scene_entity_version_id:
            declared.add(self.scene_entity_version_id)
        if self.primary_reference_entity_version_id and self.primary_reference_entity_version_id not in declared:
            raise ValueError("primary reference entity must be declared by the shot")
        return self


class DirectorOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shots: list[DirectorShotOutput] = Field(min_length=1, max_length=200)


@dataclass(frozen=True)
class DirectorSelection:
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
class DirectorResult:
    output: DirectorOutput
    raw_output: dict[str, Any]
    provider_request_id: str | None
    token_usage: dict[str, Any]


class DirectorGateway(Protocol):
    def select(self, session: Session) -> DirectorSelection: ...

    def invoke(self, selection: DirectorSelection, manifest_payload: dict[str, Any]) -> DirectorResult: ...


_DIRECTOR_SYSTEM_PROMPT = """你是片场 V2 的分镜导演智能体。你只把已接受的内容方案拆成结构化镜头候选，不与用户闲聊，不选择任何生产路由。
必须只返回一个 JSON 对象，严格符合：
{"shots":[{"shot_code":"SH-001","sequence_number":1,"duration_ms":3000,"narrative_beat_code":"BEAT_01","continuity_group_id":null,"action_count":1,"shot_type":"镜头类型","scene_entity_version_id":null,"character_entity_version_ids":[],"outfit_entity_version_ids":[],"product_entity_version_ids":[],"primary_reference_entity_version_id":null,"face_visibility":"required|optional|not_visible","text_policy":"forbidden|allowed|required","motion_requirement":"static|moderate|significant","audio_requirement":"off|lip_motion_only|configured","composition":"构图描述","action":"唯一主动作","visual_prompt":"画面生成描述","negative_prompt":null}]}
竖线分隔的是允许的枚举值，实际输出必须只选择其中一个，不得输出竖线组合或自定义描述词。
规则：
1. shot_code 必须从 SH-001 连续编号，sequence_number 从 1 连续编号。
2. narrative_beat_code 必须逐字复制输入内容节拍；每个节拍至少一个镜头，所属镜头时长之和必须精确等于该节拍时长。
3. 每个镜头只描述一个结构化主动作，action_count 必须为 1；多个动作必须拆成多个镜头。
4. 实体 ID 只能从 confirmed_entity_versions 逐字复制。不得创建、缩写、改名或猜测 ID。
5. continuity_group_id 只用于声明需要保持连续的镜头。同一组必须使用完全相同的场景、人物和服装版本 ID；不连续的镜头填 null。
6. primary_reference_entity_version_id 必须属于该镜头已声明实体，且输入事实明确提供已验证图片；没有明确主参考时填 null，不得自动采用第一个实体。
7. face_visibility、text_policy、motion_requirement 和 audio_requirement 必须显式给出，后续系统不会从描述反推。
8. audio_policy=off 时 audio_requirement 只能为 off 或 lip_motion_only，不得建立配音、对白、音乐、TTS 或音频注入依赖。
9. 不得输出 Provider、模型、工作流、NodeInfoList、价格、素材状态、任务 ID 或生产路由。
10. 不得添加提示词默认尾缀、自动负面词或输入合同中没有确认的人物、品牌、地点与产品事实。
11. 不得输出 Markdown、解释文字或 JSON 之外的内容。
"""


class ConfiguredDirectorGateway:
    def __init__(self, *, transport: AgentChatTransport | None = None, credential_resolver: EnvironmentCredentialResolver | None = None) -> None:
        self.transport = transport or HttpxAgentChatTransport()
        self.credential_resolver = credential_resolver or EnvironmentCredentialResolver.from_environment()

    def select(self, session: Session) -> DirectorSelection:
        rows = list(session.execute(
            select(ModelConfigVersion, ProviderConfigVersion, ProductionConfigVersion)
            .join(ProviderConfigVersion, ProviderConfigVersion.id == ModelConfigVersion.provider_config_version_id)
            .join(ProductionConfigVersion, ProductionConfigVersion.id == ModelConfigVersion.production_config_version_id)
            .where(
                ModelConfigVersion.agent_role == "director",
                ModelConfigVersion.status == "published",
                ProviderConfigVersion.status == "published",
                ProductionConfigVersion.status == "published",
            )
            .order_by(ModelConfigVersion.config_key, ModelConfigVersion.version_number.desc())
        ))
        latest_by_key: dict[str, tuple[ModelConfigVersion, ProviderConfigVersion, ProductionConfigVersion]] = {}
        for model, provider, config in rows:
            latest_by_key.setdefault(model.config_key, (model, provider, config))
        if not latest_by_key:
            raise AgentGatewayError("DIRECTOR_MODEL_NOT_CONFIGURED", "当前没有已发布的分镜导演模型配置。")
        if len(latest_by_key) != 1:
            raise AgentGatewayError("DIRECTOR_MODEL_SELECTION_AMBIGUOUS", "当前存在多个分镜导演模型系列，请在系统配置中保留一个明确选择。")
        model, provider, config = next(iter(latest_by_key.values()))
        if provider.adapter_kind != "openai_compatible":
            raise AgentGatewayError("DIRECTOR_ADAPTER_UNSUPPORTED", "分镜导演模型没有绑定 OpenAI-compatible 服务供应商。")
        if "text_generation" not in provider.capabilities:
            raise AgentGatewayError("DIRECTOR_CAPABILITY_MISSING", "分镜导演模型供应商未声明文本生成能力。")
        expected = {
            "input_contract_version": "director-input.v1",
            "output_schema_version": "shot-plan.v2",
            "prompt_contract_version": "director-prompt.v1",
        }
        if {key: getattr(model, key) for key in expected} != expected:
            raise AgentGatewayError("DIRECTOR_CONTRACT_VERSION_UNSUPPORTED", "分镜导演模型配置的合同版本与当前运行代码不一致。")
        return DirectorSelection(
            production_config_version_id=config.id,
            model_config_version_id=model.id,
            provider_config_version_id=provider.id,
            model_provider=provider.display_name,
            model_name=model.display_name,
            provider_model_id=model.provider_model_id,
            base_url=provider.base_url,
            credential_ref=provider.credential_ref,
            timeout_seconds=provider.request_timeout_seconds,
            input_contract_version=model.input_contract_version,
            prompt_contract_version=model.prompt_contract_version,
            output_schema_version=model.output_schema_version,
            max_output_tokens=model.max_output_tokens,
            sampling=dict(model.sampling or {}),
        )

    def invoke(self, selection: DirectorSelection, manifest_payload: dict[str, Any]) -> DirectorResult:
        if os.getenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
            raise AgentGatewayError("AGENT_MODEL_EXECUTION_DISABLED", "分镜导演模型真实调用尚未获得后端执行授权。")
        credential = self.credential_resolver.resolve(selection.credential_ref)
        if not credential.available or credential.secret is None:
            raise AgentGatewayError("AGENT_MODEL_CREDENTIAL_UNAVAILABLE", f"分镜导演模型后端凭据不可用（{credential.state}）。")
        payload: dict[str, Any] = {
            "model": selection.provider_model_id,
            "messages": [
                {"role": "system", "content": _DIRECTOR_SYSTEM_PROMPT},
                {"role": "user", "content": "以下是本次不可变分镜导演输入合同：" + json.dumps(manifest_payload, ensure_ascii=False, separators=(",", ":"))},
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
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AgentGatewayError("DIRECTOR_OUTPUT_SCHEMA_INVALID", "分镜导演输出不是有效 JSON 对象。") from exc
        try:
            output = DirectorOutput.model_validate(parsed)
            validate_director_output_against_manifest(output, manifest_payload)
        except ValidationError as exc:
            raise AgentGatewayError(
                "DIRECTOR_OUTPUT_SCHEMA_INVALID",
                "分镜导演输出不符合严格分镜合同。",
                raw_output=parsed if isinstance(parsed, dict) else None,
                diagnostics=exc.errors(include_input=False),
            ) from exc
        except ValueError as exc:
            raise AgentGatewayError("DIRECTOR_OUTPUT_CONTRACT_INVALID", str(exc), raw_output=parsed if isinstance(parsed, dict) else None) from exc
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        request_id = str(response.get("id") or "").strip() or None
        return DirectorResult(output, parsed, request_id, usage)


def validate_director_output_against_manifest(output: DirectorOutput, manifest: dict[str, Any]) -> None:
    shots = output.shots
    expected_codes = [f"SH-{index:03d}" for index in range(1, len(shots) + 1)]
    if [shot.shot_code for shot in shots] != expected_codes or [shot.sequence_number for shot in shots] != list(range(1, len(shots) + 1)):
        raise ValueError("镜头代码与顺序必须从 SH-001 和 1 连续编号。")
    beats = manifest["accepted_creative_brief"]["brief"]["narrative_beats"]
    beat_durations = {item["beat_code"]: int(item["target_duration_ms"]) for item in beats}
    actual_by_beat = {code: 0 for code in beat_durations}
    for shot in shots:
        if shot.narrative_beat_code not in beat_durations:
            raise ValueError(f"镜头 {shot.shot_code} 引用了不存在的内容节拍。")
        actual_by_beat[shot.narrative_beat_code] += shot.duration_ms
    mismatches = [code for code, duration in beat_durations.items() if actual_by_beat[code] != duration]
    if mismatches:
        raise ValueError(f"以下内容节拍的镜头时长不匹配：{mismatches}。")
    available_entities = {item["id"]: item for item in manifest["confirmed_entity_versions"]}
    continuity: dict[str, tuple[Any, ...]] = {}
    for shot in shots:
        entity_ids = {
            *(shot.character_entity_version_ids or []),
            *(shot.outfit_entity_version_ids or []),
            *(shot.product_entity_version_ids or []),
        }
        if shot.scene_entity_version_id:
            entity_ids.add(shot.scene_entity_version_id)
        unknown = sorted(entity_ids - set(available_entities))
        if unknown:
            raise ValueError(f"镜头 {shot.shot_code} 引用了未确认的实体版本：{unknown}。")
        if shot.primary_reference_entity_version_id:
            entity = available_entities[shot.primary_reference_entity_version_id]
            attachment = entity.get("source_attachment")
            if not attachment or not attachment.get("verified") or not str(attachment.get("mime_type", "")).startswith("image/"):
                raise ValueError(f"镜头 {shot.shot_code} 的主参考实体没有已验证图片。")
        if shot.continuity_group_id:
            signature = (
                shot.scene_entity_version_id,
                tuple(shot.character_entity_version_ids),
                tuple(shot.outfit_entity_version_ids),
            )
            previous = continuity.setdefault(shot.continuity_group_id, signature)
            if previous != signature:
                raise ValueError(f"连续组 {shot.continuity_group_id} 的场景、人物或服装版本不一致。")
    if manifest["audio_policy"] == "off":
        invalid = [shot.shot_code for shot in shots if shot.audio_requirement not in {"off", "lip_motion_only"}]
        if invalid:
            raise ValueError(f"音频关闭时镜头不得建立音频依赖：{invalid}。")


class DeterministicDirectorGateway:
    """Explicit test gateway; never registered by the runtime application."""

    def select(self, session: Session) -> DirectorSelection:
        return DirectorSelection(
            production_config_version_id="v2.director.test.v1",
            model_config_version_id="model_config_test_director",
            provider_config_version_id="provider_config_test_mock",
            model_provider="mock",
            model_name="deterministic-director-v1",
            provider_model_id="deterministic-director-v1",
            base_url="https://example.invalid/v1",
            credential_ref=None,
            timeout_seconds=1,
            input_contract_version="director-input.v1",
            prompt_contract_version="director-prompt.v1",
            output_schema_version="shot-plan.v2",
            max_output_tokens=None,
            sampling={},
        )

    def invoke(self, selection: DirectorSelection, manifest_payload: dict[str, Any]) -> DirectorResult:
        brief = manifest_payload["accepted_creative_brief"]["brief"]
        entity_types = {item["id"]: item["entity_type"] for item in manifest_payload["confirmed_entity_versions"]}
        referenced = [item for item in (brief.get("entity_version_ids") or []) if item in entity_types]
        characters = [item for item in referenced if entity_types[item] == "character"]
        outfits = [item for item in referenced if entity_types[item] == "outfit"]
        scenes = [item for item in referenced if entity_types[item] == "scene"]
        products = [item for item in referenced if entity_types[item] == "product"]
        shots = [
            DirectorShotOutput(
                shot_code=f"SH-{index:03d}",
                sequence_number=index,
                duration_ms=int(beat["target_duration_ms"]),
                narrative_beat_code=beat["beat_code"],
                continuity_group_id=None,
                action_count=1,
                shot_type="character_action" if characters else "concept",
                scene_entity_version_id=scenes[0] if scenes else None,
                character_entity_version_ids=characters,
                outfit_entity_version_ids=outfits,
                product_entity_version_ids=products,
                primary_reference_entity_version_id=None,
                face_visibility="optional" if characters else "not_visible",
                text_policy="forbidden",
                motion_requirement="moderate",
                audio_requirement="off" if manifest_payload["audio_policy"] == "off" else "configured",
                composition=str(beat["purpose"]),
                action=str(beat["summary"]),
                visual_prompt=f"{brief['content_promise']}。{beat['summary']}。",
                negative_prompt=None,
            )
            for index, beat in enumerate(brief["narrative_beats"], start=1)
        ]
        output = DirectorOutput(shots=shots)
        validate_director_output_against_manifest(output, manifest_payload)
        raw = output.model_dump(mode="json")
        return DirectorResult(output, raw, "test-director-request", {"total_tokens": 1})


def get_director_gateway() -> DirectorGateway:
    return ConfiguredDirectorGateway()
