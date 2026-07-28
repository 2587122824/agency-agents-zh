from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..creation.agent_gateway import AgentChatTransport, AgentGatewayError, HttpxAgentChatTransport
from ..db.models import ModelConfigVersion, ProductionConfigVersion, ProviderConfigVersion


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


CONTENT_PLANNER_INPUT_CONTRACT_VERSION = "content-planner-input.v3"
CONTENT_PLANNER_OUTPUT_SCHEMA_VERSION = "creative-brief-candidate.v4"
CONTENT_PLANNER_PROMPT_CONTRACT_VERSION = "content-planner-prompt.v7"


class ScriptSegmentBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    segment_code: str = Field(pattern=r"^SEG_[0-9]{2,3}$")
    beat_code: str = Field(pattern=r"^BEAT_[0-9]{2,3}$")


class VisualOnlyScriptSegment(ScriptSegmentBase):
    kind: Literal["visual_only"]


class OnScreenTextScriptSegment(ScriptSegmentBase):
    kind: Literal["on_screen_text"]
    on_screen_text: str = Field(min_length=1, max_length=500)


class VoiceoverScriptSegment(ScriptSegmentBase):
    kind: Literal["voiceover"]
    spoken_text: str = Field(min_length=1, max_length=4000)


class DialogueScriptSegment(ScriptSegmentBase):
    kind: Literal["dialogue"]
    spoken_text: str = Field(min_length=1, max_length=4000)


ScriptSegment = Annotated[
    VisualOnlyScriptSegment | OnScreenTextScriptSegment | VoiceoverScriptSegment | DialogueScriptSegment,
    Field(discriminator="kind"),
]


class BriefQuestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    option_code: str = Field(pattern=r"^OPTION_[0-9]{2}$")
    label: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=500)
    answer: str = Field(min_length=1, max_length=1000)


class BriefOpenQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question_code: str = Field(pattern=r"^QUESTION_[0-9]{2}$")
    prompt: str = Field(min_length=1, max_length=500)
    reason: str = Field(min_length=1, max_length=500)
    options: list[BriefQuestionOption] = Field(min_length=2, max_length=3)

    @model_validator(mode="after")
    def options_are_consistent(self):
        option_codes = [item.option_code for item in self.options]
        if len(option_codes) != len(set(option_codes)):
            raise ValueError("open question option codes must be unique")
        expected_codes = [f"OPTION_{index:02d}" for index in range(1, len(self.options) + 1)]
        if option_codes != expected_codes:
            raise ValueError("open question option codes must be consecutive")
        if len({item.answer for item in self.options}) != len(self.options):
            raise ValueError("open question option answers must be distinct")
        return self


class CreativeBasisReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["requirement_field", "decision", "entity_version"]
    reference_id: str = Field(min_length=1, max_length=200)


class CreativeAddition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    addition_code: str = Field(pattern=r"^ADDITION_[0-9]{2}$")
    category: Literal["narrative_structure", "hook", "expression", "example", "visual_strategy", "call_to_action"]
    content: str = Field(min_length=1, max_length=1000)
    purpose: str = Field(min_length=1, max_length=500)
    basis_refs: list[CreativeBasisReference] = Field(min_length=1, max_length=20)


class BriefPendingFact(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fact_code: str = Field(pattern=r"^FACT_[0-9]{2}$")
    statement: str = Field(min_length=1, max_length=1000)
    reason: str = Field(min_length=1, max_length=500)
    resolution_question_code: str = Field(pattern=r"^QUESTION_[0-9]{2}$")


class ContentPlannerOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(min_length=1, max_length=160)
    content_promise: str = Field(min_length=1, max_length=500)
    audience_takeaway: str = Field(min_length=1, max_length=500)
    hook: BriefHook
    narrative_beats: list[NarrativeBeat] = Field(min_length=1, max_length=12)
    script_segments: list[ScriptSegment] = Field(min_length=1, max_length=36)
    tone: str = Field(min_length=1, max_length=200)
    pacing: str = Field(min_length=1, max_length=200)
    platform_adaptation: str | None = Field(default=None, min_length=1, max_length=1000)
    entity_version_ids: list[str] = Field(default_factory=list, max_length=100)
    constraints_carried_forward: list[str] = Field(default_factory=list, max_length=50)
    creative_additions: list[CreativeAddition] = Field(max_length=8)
    facts_requiring_confirmation: list[BriefPendingFact] = Field(max_length=5)
    open_questions: list[BriefOpenQuestion] = Field(default_factory=list, max_length=5)

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
        question_codes = [item.question_code for item in self.open_questions]
        if len(question_codes) != len(set(question_codes)):
            raise ValueError("open question codes must be unique")
        expected_question_codes = [f"QUESTION_{index:02d}" for index in range(1, len(self.open_questions) + 1)]
        if question_codes != expected_question_codes:
            raise ValueError("open question codes must be consecutive")
        addition_codes = [item.addition_code for item in self.creative_additions]
        expected_addition_codes = [f"ADDITION_{index:02d}" for index in range(1, len(self.creative_additions) + 1)]
        if addition_codes != expected_addition_codes:
            raise ValueError("creative addition codes must be unique and consecutive")
        fact_codes = [item.fact_code for item in self.facts_requiring_confirmation]
        expected_fact_codes = [f"FACT_{index:02d}" for index in range(1, len(self.facts_requiring_confirmation) + 1)]
        if fact_codes != expected_fact_codes:
            raise ValueError("pending fact codes must be unique and consecutive")
        resolution_codes = [item.resolution_question_code for item in self.facts_requiring_confirmation]
        if len(resolution_codes) != len(set(resolution_codes)):
            raise ValueError("each pending fact must use a distinct resolution question")
        unknown_questions = sorted(set(resolution_codes) - set(question_codes))
        if unknown_questions:
            raise ValueError(f"pending facts reference unknown questions: {unknown_questions}")
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
    api_key: str | None
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


_SYSTEM_PROMPT_RULES = """你是片场 V2 的内容策划智能体。你根据已确认需求主动拓展叙事结构、开场、表达方式、内容示例、视觉策略和行动引导，形成可拍摄的内容策略与脚本结构；不与用户闲聊，不生成镜头，不选择生产参数。
必须只返回一个 JSON 对象，并逐字段遵守末尾的权威 JSON Schema。JSON Schema 由运行时输出模型直接生成，是字段、枚举、必填项、互斥结构和附加字段规则的唯一权威来源。
规则：
1. 只读取输入合同中的已确认需求、已解决决策、精确实体版本与交付约束；不得读取或假设自由聊天内容。
2. narrative_beats 的 target_duration_ms 总和必须精确等于 delivery_constraints.duration_ms；代码必须从 BEAT_01 连续编号。
3. script_segments 只能引用已存在的 beat_code，代码必须从 SEG_01 连续编号。四种 kind 是互斥对象：visual_only 只允许 segment_code、beat_code、kind；on_screen_text 只额外要求 on_screen_text；voiceover 和 dialogue 只额外要求 spoken_text。需要同时表达纯画面与画面文字时，必须拆成两个连续脚本段，不得在一个对象中混合字段。
4. entity_version_ids 只能从 confirmed_entity_versions 中逐字复制，不得创建、缩写、改名或猜测实体 ID。
5. audio_policy=off 时 kind 只能为 visual_only 或 on_screen_text，任何脚本段都不得出现 spoken_text 字段；不得建立旁白、对白、音乐、TTS 或对口型依赖。
6. platform 为 null 时 platform_adaptation 必须为 null；platform 有明确值时 platform_adaptation 必须明确说明如何适配该平台，不得遗漏。
7. 不得输出镜头 ID、画面提示词、Provider、模型、工作流、NodeInfoList、价格、素材状态或生产任务。
8. 你应主动进行创意拓展，但每项拓展必须写入 creative_additions，并通过 basis_refs 明确引用输入合同中的真实来源。type=requirement_field 时 reference_id 只能逐字复制 requirement_version.fields 的直接字段键，例如 core_topic、duration_seconds、creative_direction，绝不能写 requirement ID、完整 JSON 路径或 requirement_xxx.fields.duration_seconds；type=decision 时只能逐字复制 confirmed_decisions[].id；type=entity_version 时只能逐字复制 confirmed_entity_versions[].id。创意拓展可以提出叙事组织、表达方式和视觉策略，不得把创意设想伪装成已经确认的事实。
9. 输入合同未确认的数字、结果、身份、经历、时间、地点、品牌或产品信息，如方案确实需要采用，必须同时写入 facts_requiring_confirmation 和 open_questions。每条待确认事实精确关联一个独立问题；用户修订确认后，该事实不得继续留在 facts_requiring_confirmation。不得隐藏、改写或默认确认事实。
10. 只有确实需要用户决定且会改变内容方案时才写入 open_questions。每个问题必须提供 2 到 3 个互斥、可直接执行的答案选项；问题、选项和答案代码必须从 01 连续编号。不得提供“其他”选项，页面会独立提供自定义回答。
11. constraints_carried_forward 是可选的可读说明，只能登记输入合同中真实存在的约束，不得创造默认规则；该字段为空不代表约束失效，后端按不可变输入合同直接验收实际输出。
12. 不得输出 Markdown 代码块、解释文字或 JSON 之外的内容。
13. 输入存在 revision_request 时，source_brief 是待调整的冻结原方案，instruction 是用户本轮唯一修改意见。你必须在继续满足已确认需求与全部确定性合同的前提下修改原方案；instruction 中逐项确认的答案可用于解决对应 open_questions，但不得改动需求版本、音频策略、时长、画幅、实体白名单或生产路由。输入不存在 revision_request 时按首次策划处理。
14. 保持方案紧凑：每个叙事节拍通常使用 1 到 4 个脚本段，只保留会改变内容结构的策划拓展和待确认项。不得通过重复摘要、重复文字段或同义拓展填充输出。
15. production_profile 是用户在项目创建前确认的不可变生产边界。video_motion_strategy=three_frame 且 enforcement=required 时，节拍与脚本必须能继续拆成短镜头、每镜头一个主动作，并允许分镜导演为动作开始、关键过程和结束分别定义画面状态；不得设计依赖一个长镜头连续完成多个不可分动作的内容。你仍不生成镜头或选择工作流。
"""


_SYSTEM_PROMPT = (
    _SYSTEM_PROMPT_RULES
    + "\n权威 JSON Schema：\n"
    + json.dumps(ContentPlannerOutput.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
)


def select_content_planner_configuration(session: Session) -> ContentPlannerSelection:
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
        "input_contract_version": CONTENT_PLANNER_INPUT_CONTRACT_VERSION,
        "output_schema_version": CONTENT_PLANNER_OUTPUT_SCHEMA_VERSION,
        "prompt_contract_version": CONTENT_PLANNER_PROMPT_CONTRACT_VERSION,
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
        api_key=provider.api_key,
        timeout_seconds=provider.request_timeout_seconds,
        input_contract_version=model.input_contract_version,
        prompt_contract_version=model.prompt_contract_version,
        output_schema_version=model.output_schema_version,
        max_output_tokens=model.max_output_tokens,
        sampling=dict(model.sampling or {}),
    )


class ConfiguredContentPlannerGateway:
    def __init__(
        self,
        *,
        transport: AgentChatTransport | None = None,
    ) -> None:
        self.transport = transport or HttpxAgentChatTransport()

    def select(self, session: Session) -> ContentPlannerSelection:
        return select_content_planner_configuration(session)

    def invoke(
        self,
        selection: ContentPlannerSelection,
        manifest_payload: dict[str, Any],
    ) -> ContentPlannerResult:
        if os.getenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
            raise AgentGatewayError("AGENT_MODEL_EXECUTION_DISABLED", "内容策划模型真实调用尚未获得后端执行授权。")
        api_key = str(selection.api_key or "").strip()
        if not api_key:
            raise AgentGatewayError("AGENT_MODEL_CREDENTIAL_UNAVAILABLE", "内容策划模型供应商的 API Key 未填写。")
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
            api_key=api_key,
            payload=payload,
            timeout_seconds=selection.timeout_seconds,
        )
        request_id = str(response.get("id") or "").strip() or None
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AgentGatewayError(
                "CONTENT_PLANNER_RESPONSE_CONTENT_MISSING",
                "内容策划模型响应中没有可解析的文本内容。",
                raw_output={"provider_response": response},
                diagnostics=[{"type": "response_content_missing", "detail": str(exc)}],
                provider_request_id=request_id,
                token_usage=usage,
            ) from exc
        if not isinstance(content, str) or not content.strip():
            raise AgentGatewayError(
                "CONTENT_PLANNER_RESPONSE_CONTENT_MISSING",
                "内容策划模型响应中的文本内容为空。",
                raw_output={"provider_response": response},
                diagnostics=[{"type": "response_content_empty"}],
                provider_request_id=request_id,
                token_usage=usage,
            )
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as exc:
            choice = response.get("choices", [{}])[0]
            raise AgentGatewayError(
                "CONTENT_PLANNER_OUTPUT_JSON_INVALID",
                "内容策划模型返回的文本不是有效 JSON。",
                raw_output={
                    "content": content,
                    "finish_reason": choice.get("finish_reason") if isinstance(choice, dict) else None,
                },
                diagnostics=[{
                    "type": "json_decode_error",
                    "message": exc.msg,
                    "line": exc.lineno,
                    "column": exc.colno,
                    "position": exc.pos,
                }],
                provider_request_id=request_id,
                token_usage=usage,
            ) from exc
        try:
            output = ContentPlannerOutput.model_validate(parsed)
            _validate_output_against_manifest(output, manifest_payload)
        except ValidationError as exc:
            raise AgentGatewayError(
                "CONTENT_PLANNER_OUTPUT_SCHEMA_INVALID",
                "内容策划模型输出不符合严格 Brief 合同。",
                raw_output=parsed if isinstance(parsed, dict) else None,
                diagnostics=exc.errors(include_input=False),
                provider_request_id=request_id,
                token_usage=usage,
            ) from exc
        except ValueError as exc:
            raise AgentGatewayError(
                "CONTENT_PLANNER_OUTPUT_CONTRACT_INVALID",
                str(exc),
                raw_output=parsed if isinstance(parsed, dict) else None,
                provider_request_id=request_id,
                token_usage=usage,
            ) from exc
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
        invalid_audio = [item.segment_code for item in output.script_segments if item.kind in {"voiceover", "dialogue"}]
        if invalid_audio:
            raise ValueError(f"音频关闭时脚本段不得包含口播或对白：{invalid_audio}。")
    if manifest.get("platform") is None and output.platform_adaptation is not None:
        raise ValueError("平台未指定时不得生成平台适配。")
    if manifest.get("platform") is not None and output.platform_adaptation is None:
        raise ValueError("平台已明确指定时必须生成对应的平台适配。")
    reference_sets = {
        "requirement_field": set(manifest["requirement_version"]["fields"]),
        "decision": {item["id"] for item in manifest["confirmed_decisions"]},
        "entity_version": {item["id"] for item in manifest["confirmed_entity_versions"]},
    }
    for addition in output.creative_additions:
        for reference in addition.basis_refs:
            if reference.reference_id not in reference_sets[reference.type]:
                raise ValueError(
                    f"策划拓展 {addition.addition_code} 引用了不存在的 {reference.type}：{reference.reference_id}。"
                )


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
            api_key=None,
            timeout_seconds=1,
            input_contract_version=CONTENT_PLANNER_INPUT_CONTRACT_VERSION,
            prompt_contract_version=CONTENT_PLANNER_PROMPT_CONTRACT_VERSION,
            output_schema_version=CONTENT_PLANNER_OUTPUT_SCHEMA_VERSION,
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
            VisualOnlyScriptSegment(
                segment_code=f"SEG_{index:02d}", beat_code=beat.beat_code, kind="visual_only"
            ) if audio_off else VoiceoverScriptSegment(
                segment_code=f"SEG_{index:02d}", beat_code=beat.beat_code, kind="voiceover", spoken_text=beat.summary
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
            creative_additions=[CreativeAddition(
                addition_code="ADDITION_01",
                category="narrative_structure",
                content="按目标建立、过程展开和结果收束组织完整内容。",
                purpose="让内容推进清晰并覆盖完整目标时长。",
                basis_refs=[CreativeBasisReference(type="requirement_field", reference_id="core_topic")],
            )],
            facts_requiring_confirmation=[],
            open_questions=[],
        )
        return ContentPlannerResult(output, output.model_dump(mode="json"), "test-planner-request", {"total_tokens": 1})


def get_content_planner_gateway() -> ContentPlannerGateway:
    return ConfiguredContentPlannerGateway()
