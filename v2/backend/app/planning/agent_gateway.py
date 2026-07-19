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


class BriefHook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["visual_action", "question", "contrast", "result", "statement"]
    content: str = Field(min_length=1, max_length=500)


class NarrativeBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    beat_code: str = Field(pattern=r"^BEAT_[0-9]{2,3}$")
    purpose: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=1000)
    target_duration_ms: int = Field(ge=500, le=3_600_000)


class ScriptSegment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_code: str = Field(pattern=r"^SEG_[0-9]{2,3}$")
    beat_code: str = Field(pattern=r"^BEAT_[0-9]{2,3}$")
    kind: Literal["visual_only", "voiceover", "dialogue", "on_screen_text"]
    spoken_text: str | None = Field(default=None, min_length=1, max_length=4000)
    on_screen_text: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def content_matches_kind(self):
        if self.kind in {"voiceover", "dialogue"} and self.spoken_text is None:
            raise ValueError("spoken_text is required for voiceover and dialogue segments")
        if self.kind == "visual_only" and (self.spoken_text is not None or self.on_screen_text is not None):
            raise ValueError("visual_only segments cannot contain spoken or on-screen text")
        if self.kind == "on_screen_text" and self.on_screen_text is None:
            raise ValueError("on_screen_text is required for on_screen_text segments")
        return self


class ContentPlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    content_promise: str = Field(min_length=1, max_length=500)
    audience_takeaway: str = Field(min_length=1, max_length=500)
    hook: BriefHook
    narrative_beats: list[NarrativeBeat] = Field(min_length=1, max_length=30)
    script_segments: list[ScriptSegment] = Field(min_length=1, max_length=80)
    tone: str = Field(min_length=1, max_length=200)
    pacing: str = Field(min_length=1, max_length=200)
    platform_adaptation: str | None = Field(default=None, min_length=1, max_length=1000)
    entity_version_ids: list[str] = Field(default_factory=list, max_length=100)
    constraints_carried_forward: list[str] = Field(default_factory=list, max_length=50)
    open_questions: list[str] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def references_are_consistent(self):
        beat_codes = [item.beat_code for item in self.narrative_beats]
        segment_codes = [item.segment_code for item in self.script_segments]
        if len(beat_codes) != len(set(beat_codes)):
            raise ValueError("narrative beat codes must be unique")
        if len(segment_codes) != len(set(segment_codes)):
            raise ValueError("script segment codes must be unique")
        unknown_beats = sorted({item.beat_code for item in self.script_segments} - set(beat_codes))
        if unknown_beats:
            raise ValueError(f"script segments reference unknown beats: {unknown_beats}")
        missing_beats = sorted(set(beat_codes) - {item.beat_code for item in self.script_segments})
        if missing_beats:
            raise ValueError(f"narrative beats are missing script segments: {missing_beats}")
        if len(self.entity_version_ids) != len(set(self.entity_version_ids)):
            raise ValueError("entity_version_ids must be unique")
        if len(self.constraints_carried_forward) != len(set(self.constraints_carried_forward)):
            raise ValueError("constraints_carried_forward must be unique")
        if len(self.open_questions) != len(set(self.open_questions)):
            raise ValueError("open_questions must be unique")
        return self


@dataclass(frozen=True)
class ContentPlannerSelection:
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
class ContentPlannerResult:
    output: ContentPlannerOutput
    raw_output: dict[str, Any]
    provider_request_id: str | None
    token_usage: dict[str, Any]


class ContentPlannerGateway(Protocol):
    def select(self, session: Session) -> ContentPlannerSelection: ...

    def invoke(
        self,
        selection: ContentPlannerSelection,
        manifest_payload: dict[str, Any],
    ) -> ContentPlannerResult: ...


_SYSTEM_PROMPT = """你是片场 V2 的内容策划智能体。你只把已确认需求组织成可拍摄的内容策略和脚本结构，不与用户闲聊，不生成镜头，不选择生产参数。
必须只返回一个 JSON 对象，严格符合：
{"title":"方案标题","content_promise":"内容承诺","audience_takeaway":"观众收获","hook":{"kind":"visual_action|question|contrast|result|statement","content":"开场钩子"},"narrative_beats":[{"beat_code":"BEAT_01","purpose":"节拍目的","summary":"内容摘要","target_duration_ms":5000}],"script_segments":[{"segment_code":"SEG_01","beat_code":"BEAT_01","kind":"visual_only|voiceover|dialogue|on_screen_text","spoken_text":null,"on_screen_text":null}],"tone":"语气","pacing":"节奏","platform_adaptation":null,"entity_version_ids":[],"constraints_carried_forward":[],"open_questions":[]}
规则：
1. 只读取输入合同中的已确认需求、已解决决策、精确实体版本与交付约束；不得读取或假设自由聊天内容。
2. narrative_beats 的 target_duration_ms 总和必须精确等于 delivery_constraints.duration_ms；代码必须从 BEAT_01 连续编号。
3. script_segments 只能引用已存在的 beat_code，代码必须从 SEG_01 连续编号。
4. entity_version_ids 只能从 confirmed_entity_versions 中逐字复制，不得创建、缩写、改名或猜测实体 ID。
5. audio_policy=off 时所有 spoken_text 必须为 null，kind 不得为 voiceover 或 dialogue；不得建立旁白、对白、音乐、TTS 或对口型依赖。
6. platform 为 null 时 platform_adaptation 必须为 null，不得默认适配任何平台。
7. 不得输出镜头 ID、画面提示词、Provider、模型、工作流、NodeInfoList、价格、素材状态或生产任务。
8. 不得引入输入合同中没有确认的人物、品牌、地点或产品事实。信息不足时写入 open_questions，不得自行补写事实。
9. constraints_carried_forward 是可选的可读说明，只能登记输入合同中真实存在的约束，不得创造默认规则；该字段为空不代表约束失效，后端按不可变输入合同直接验收实际输出。
10. 不得输出 Markdown 代码块、解释文字或 JSON 之外的内容。
11. 输入存在 revision_request 时，source_brief 是待调整的冻结原方案，instruction 是用户本轮唯一修改意见。你必须在继续满足已确认需求与全部确定性合同的前提下修改原方案，不得把 instruction 当作新的项目事实，不得改动需求版本、音频策略、时长、画幅、实体白名单或生产路由。输入不存在 revision_request 时按首次策划处理。
"""


class ConfiguredContentPlannerGateway:
    def __init__(
        self,
        *,
        transport: AgentChatTransport | None = None,
        credential_resolver: EnvironmentCredentialResolver | None = None,
    ) -> None:
        self.transport = transport or HttpxAgentChatTransport()
        self.credential_resolver = credential_resolver or EnvironmentCredentialResolver.from_environment()

    def select(self, session: Session) -> ContentPlannerSelection:
        rows = list(session.execute(
            select(ModelConfigVersion, ProviderConfigVersion, ProductionConfigVersion)
            .join(ProviderConfigVersion, ProviderConfigVersion.id == ModelConfigVersion.provider_config_version_id)
            .join(ProductionConfigVersion, ProductionConfigVersion.id == ModelConfigVersion.production_config_version_id)
            .where(
                ModelConfigVersion.agent_role == "planner",
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
            raise AgentGatewayError("CONTENT_PLANNER_MODEL_NOT_CONFIGURED", "当前没有已发布的内容策划模型配置。")
        if len(latest_by_key) != 1:
            raise AgentGatewayError("CONTENT_PLANNER_MODEL_SELECTION_AMBIGUOUS", "当前存在多个内容策划模型系列，请在系统配置中保留一个明确选择。")
        model, provider, config = next(iter(latest_by_key.values()))
        if provider.adapter_kind != "openai_compatible":
            raise AgentGatewayError("CONTENT_PLANNER_ADAPTER_UNSUPPORTED", "内容策划模型没有绑定 OpenAI-compatible 服务供应商。")
        if "text_generation" not in provider.capabilities:
            raise AgentGatewayError("CONTENT_PLANNER_CAPABILITY_MISSING", "内容策划模型供应商未声明文本生成能力。")
        expected = {
            "input_contract_version": "content-planner-input.v2",
            "output_schema_version": "creative-brief-candidate.v1",
            "prompt_contract_version": "content-planner-prompt.v2",
        }
        actual = {key: getattr(model, key) for key in expected}
        if actual != expected:
            raise AgentGatewayError("CONTENT_PLANNER_CONTRACT_VERSION_UNSUPPORTED", "内容策划模型配置的合同版本与当前运行代码不一致。")
        return ContentPlannerSelection(
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

    def invoke(
        self,
        selection: ContentPlannerSelection,
        manifest_payload: dict[str, Any],
    ) -> ContentPlannerResult:
        if os.getenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
            raise AgentGatewayError("AGENT_MODEL_EXECUTION_DISABLED", "内容策划模型真实调用尚未获得后端执行授权。")
        credential = self.credential_resolver.resolve(selection.credential_ref)
        if not credential.available or credential.secret is None:
            raise AgentGatewayError("AGENT_MODEL_CREDENTIAL_UNAVAILABLE", f"内容策划模型后端凭据不可用（{credential.state}）。")
        payload: dict[str, Any] = {
            "model": selection.provider_model_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": "以下是本次不可变内容策划输入合同："
                    + json.dumps(manifest_payload, ensure_ascii=False, separators=(",", ":")),
                },
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
            content = response["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AgentGatewayError("CONTENT_PLANNER_OUTPUT_SCHEMA_INVALID", "内容策划模型输出不是有效 JSON 对象。") from exc
        try:
            output = ContentPlannerOutput.model_validate(parsed)
            _validate_output_against_manifest(output, manifest_payload)
        except ValidationError as exc:
            raise AgentGatewayError(
                "CONTENT_PLANNER_OUTPUT_SCHEMA_INVALID",
                "内容策划模型输出不符合严格 Brief 合同。",
                raw_output=parsed if isinstance(parsed, dict) else None,
                diagnostics=exc.errors(include_input=False),
            ) from exc
        except ValueError as exc:
            raise AgentGatewayError(
                "CONTENT_PLANNER_OUTPUT_CONTRACT_INVALID",
                str(exc),
                raw_output=parsed if isinstance(parsed, dict) else None,
            ) from exc
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        request_id = str(response.get("id") or "").strip() or None
        return ContentPlannerResult(output, parsed, request_id, usage)


def _validate_output_against_manifest(output: ContentPlannerOutput, manifest: dict[str, Any]) -> None:
    expected_duration = int(manifest["delivery_constraints"]["duration_ms"])
    actual_duration = sum(item.target_duration_ms for item in output.narrative_beats)
    if actual_duration != expected_duration:
        raise ValueError(f"内容节拍总时长 {actual_duration}ms 与目标 {expected_duration}ms 不一致。")
    expected_beats = [f"BEAT_{index:02d}" for index in range(1, len(output.narrative_beats) + 1)]
    if [item.beat_code for item in output.narrative_beats] != expected_beats:
        raise ValueError("内容节拍代码必须从 BEAT_01 连续编号。")
    expected_segments = [f"SEG_{index:02d}" for index in range(1, len(output.script_segments) + 1)]
    if [item.segment_code for item in output.script_segments] != expected_segments:
        raise ValueError("脚本段代码必须从 SEG_01 连续编号。")
    available_entities = {item["id"] for item in manifest["confirmed_entity_versions"]}
    unknown_entities = sorted(set(output.entity_version_ids) - available_entities)
    if unknown_entities:
        raise ValueError(f"内容策划引用了未确认的实体版本：{unknown_entities}。")
    if manifest["audio_policy"] == "off":
        invalid_audio = [item.segment_code for item in output.script_segments if item.spoken_text is not None or item.kind in {"voiceover", "dialogue"}]
        if invalid_audio:
            raise ValueError(f"音频关闭时脚本段不得包含口播或对白：{invalid_audio}。")
    if manifest.get("platform") is None and output.platform_adaptation is not None:
        raise ValueError("平台未指定时不得生成平台适配。")


class DeterministicContentPlannerGateway:
    """Explicit test gateway; never registered by the runtime application."""

    def select(self, session: Session) -> ContentPlannerSelection:
        return ContentPlannerSelection(
            production_config_version_id="v2.planner.test.v1",
            model_config_version_id="model_config_test_planner",
            provider_config_version_id="provider_config_test_mock",
            model_provider="mock",
            model_name="deterministic-content-planner-v1",
            provider_model_id="deterministic-content-planner-v1",
            base_url="https://example.invalid/v1",
            credential_ref=None,
            timeout_seconds=1,
            input_contract_version="content-planner-input.v2",
            prompt_contract_version="content-planner-prompt.v2",
            output_schema_version="creative-brief-candidate.v1",
            max_output_tokens=None,
            sampling={},
        )

    def invoke(self, selection: ContentPlannerSelection, manifest_payload: dict[str, Any]) -> ContentPlannerResult:
        duration = int(manifest_payload["delivery_constraints"]["duration_ms"])
        first = duration * 30 // 100
        second = duration * 40 // 100
        durations = [first, second, duration - first - second]
        requirement = manifest_payload["requirement_version"]["fields"]
        audio_off = manifest_payload["audio_policy"] == "off"
        beats = [
            NarrativeBeat(beat_code=f"BEAT_{index:02d}", purpose=purpose, summary=summary, target_duration_ms=durations[index - 1])
            for index, (purpose, summary) in enumerate([
                ("建立目标", "快速建立内容主题与观看理由"),
                ("展开内容", "用主要过程兑现内容承诺"),
                ("完成收束", "回到结果并留下明确记忆点"),
            ], start=1)
        ]
        segments = [
            ScriptSegment(
                segment_code=f"SEG_{index:02d}",
                beat_code=beat.beat_code,
                kind="visual_only" if audio_off else "voiceover",
                spoken_text=None if audio_off else beat.summary,
                on_screen_text=None,
            )
            for index, beat in enumerate(beats, start=1)
        ]
        output = ContentPlannerOutput(
            title=f"{requirement['core_topic']}内容方案" + ("（调整版）" if manifest_payload.get("revision_request") else ""),
            content_promise=(
                f"围绕{requirement['core_topic']}形成完整、清晰的内容推进。"
                + (f" 调整重点：{manifest_payload['revision_request']['instruction']}" if manifest_payload.get("revision_request") else "")
            ),
            audience_takeaway="观众能够理解主题、过程与最终结果。",
            hook=BriefHook(kind="visual_action", content="用一个直接动作快速建立主题。"),
            narrative_beats=beats,
            script_segments=segments,
            tone=str(requirement.get("tone") or "清晰、自然"),
            pacing="开场紧凑，中段充分，结尾利落",
            platform_adaptation=None if manifest_payload.get("platform") is None else f"适配 {manifest_payload['platform']}",
            entity_version_ids=[item["id"] for item in manifest_payload["confirmed_entity_versions"]],
            constraints_carried_forward=[f"audio_policy={manifest_payload['audio_policy']}"],
            open_questions=[],
        )
        return ContentPlannerResult(output, output.model_dump(mode="json"), "test-planner-request", {"total_tokens": 1})


def get_content_planner_gateway() -> ContentPlannerGateway:
    return ConfiguredContentPlannerGateway()
