from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, Protocol
from urllib.parse import urljoin

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ModelConfigVersion, ProductionConfigVersion, ProviderConfigVersion
if TYPE_CHECKING:
    from ..providers.credentials import EnvironmentCredentialResolver


class AgentGatewayError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        raw_output: dict[str, Any] | None = None,
        diagnostics: list[dict[str, Any]] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.raw_output = raw_output
        self.diagnostics = diagnostics or []


CREATIVE_FIELD_KEYS = Literal[
    "title",
    "core_topic",
    "content_goal",
    "platform",
    "target_audience",
    "duration_seconds",
    "aspect_ratio",
    "audio_mode",
    "visual_style",
    "tone",
    "content_structure",
    "call_to_action",
    "creative_direction",
    "creative_constraints",
]

CREATIVE_PROJECT_TYPES = Literal[
    "personal_record",
    "promotion",
    "knowledge",
    "narrative",
    "brand_story",
    "emotional_expression",
    "other",
]

CREATIVE_STAGES = Literal["exploring", "shaping", "refining", "ready_to_confirm"]


class ProposedFieldUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_key: CREATIVE_FIELD_KEYS
    value: Any
    source_message_ids: list[str] = Field(min_length=1, max_length=8)


class CreativeSuggestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=60)
    summary: str = Field(min_length=1, max_length=240)
    value: Any


class CreativeSuggestionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=120)
    field_key: CREATIVE_FIELD_KEYS
    source_message_ids: list[str] = Field(min_length=1, max_length=8)
    options: list[CreativeSuggestionOption] = Field(min_length=2, max_length=3)


class CreativeProposalSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1, max_length=48)
    suggestion_set_id: str = Field(min_length=1, max_length=48)
    option_id: str = Field(min_length=1, max_length=48)
    source_message_ids: list[str] = Field(min_length=1, max_length=8)


class CreativeGap(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_key: CREATIVE_FIELD_KEYS
    reason: str = Field(min_length=1, max_length=160)


class CreativeDiagnosis(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_type: CREATIVE_PROJECT_TYPES
    stage: CREATIVE_STAGES
    summary: str = Field(min_length=1, max_length=240)
    established_fields: list[CREATIVE_FIELD_KEYS] = Field(default_factory=list, max_length=14)
    open_gaps: list[CreativeGap] = Field(default_factory=list, max_length=6)
    focus_field: CREATIVE_FIELD_KEYS | None = None
    focus_reason: str = Field(min_length=1, max_length=200)
    source_message_ids: list[str] = Field(min_length=1, max_length=8)


class CreativeAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_reply: str = Field(min_length=1, max_length=8000)
    creative_diagnosis: CreativeDiagnosis
    suggestion_sets: list[CreativeSuggestionSet] = Field(default_factory=list, max_length=3)
    proposal_selections: list[CreativeProposalSelection] = Field(default_factory=list, max_length=3)
    explicit_updates: list[ProposedFieldUpdate] = Field(default_factory=list, max_length=16)
    clarifying_question: str | None = Field(default=None, max_length=300)


@dataclass(frozen=True)
class CreativeAgentSelection:
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
class CreativeAgentResult:
    output: CreativeAgentOutput
    raw_output: dict[str, Any]
    provider_request_id: str | None
    token_usage: dict[str, Any]


class CreativeAgentGateway(Protocol):
    def select(self, session: Session) -> CreativeAgentSelection: ...

    def invoke(
        self,
        selection: CreativeAgentSelection,
        manifest_payload: dict[str, Any],
    ) -> CreativeAgentResult: ...


class AgentChatTransport(Protocol):
    def create_chat_completion(
        self,
        *,
        url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]: ...


class HttpxAgentChatTransport:
    def create_chat_completion(
        self,
        *,
        url: str,
        api_key: str,
        payload: dict[str, Any],
        timeout_seconds: int,
    ) -> dict[str, Any]:
        try:
            response = httpx.post(
                url,
                headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
                json=payload,
                timeout=timeout_seconds,
            )
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise AgentGatewayError("AGENT_MODEL_HTTP_FAILED", "创作模型请求失败。") from exc
        if not isinstance(data, dict):
            raise AgentGatewayError("AGENT_MODEL_RESPONSE_INVALID", "创作模型返回的响应不是对象。")
        return data


CREATIVE_INPUT_CONTRACT_VERSION = "v2.creative-dialogue-input.v5"
CREATIVE_OUTPUT_SCHEMA_VERSION = "v2.creative-dialogue-output.v5"
CREATIVE_PROMPT_CONTRACT_VERSION = "v2.creative-dialogue-prompt.v13"


_SYSTEM_PROMPT = """你是片场 V2 的创作制片人。你负责自然对话、理解需求、主动提出创意选择和登记用户明确表达，不执行脚本策划、分镜或生产。
必须只返回一个 JSON 对象，严格符合：
{"assistant_reply":"中文回复","creative_diagnosis":{"project_type":"personal_record|promotion|knowledge|narrative|brand_story|emotional_expression|other","stage":"exploring|shaping|refining|ready_to_confirm","summary":"当前创作判断","established_fields":["已明确字段"],"open_gaps":[{"field_key":"待讨论字段","reason":"为什么重要"}],"focus_field":"本轮最值得讨论的字段或null","focus_reason":"本轮聚焦原因","source_message_ids":["依据消息ID"]},"suggestion_sets":[{"category":"类别","title":"问题","field_key":"本组唯一修改字段","source_message_ids":["用户消息ID"],"options":[{"label":"选项名","summary":"差异说明","value":"该字段的建议值"}]}],"proposal_selections":[{"proposal_id":"已有提案ID","suggestion_set_id":"已有建议组ID","option_id":"已有选项ID","source_message_ids":["用户选择消息ID"]}],"explicit_updates":[{"field_key":"允许字段","value":"用户明确值","source_message_ids":["用户消息ID"]}],"clarifying_question":null}
允许字段及用途：
- title 项目名称；core_topic 核心主题；content_goal 内容目标；platform 发布平台；target_audience 目标受众。
- duration_seconds 目标秒数；aspect_ratio 画幅；audio_mode 仅 off 或 voiceover。
- visual_style 视觉风格；tone 情绪语气；content_structure 内容结构；call_to_action 结尾行动号召。
- creative_direction 整体创作方向；creative_constraints 用户明确限制的字符串列表。
合法建议示例：
{"category":"content_direction","title":"你希望采用哪种内容结构？","field_key":"content_structure","source_message_ids":["最新用户消息ID"],"options":[{"label":"训练日记","summary":"按准备、训练、完成推进","value":"训练日记"},{"label":"挑战记录","summary":"突出目标和结果对比","value":"挑战记录"}]}
规则：
1. 读取 conversation 中按顺序提供的用户和助手消息；proposal_history 是不含系统 ID 的只读历史，助手消息和未选择建议都不是用户事实，history.selections 才表示用户曾经作出的选择。
2. 只有用户明确表达的值才能进入 explicit_updates，且 source_message_ids 只能引用 role=user 的消息。
2.1 最新用户消息同时包含明确事实或限制与建议请求时，两部分必须独立处理：明确内容进入 explicit_updates，建议进入 suggestion_sets；不得因为已经给出建议而漏掉用户明确表达。
2.2 explicit_updates 不得重复 active_requirement 中已经相同的字段值；保持现状可以在自然回复中确认，但不能伪装成字段变更。
3. 用户要求建议时直接给 2 到 3 个互斥且有明显差异的选项，推荐项放第一；建议不能进入 explicit_updates。
4. suggestion_sets 每组只能有 2 到 3 个选项；每组必须声明唯一 field_key 和来源消息，每个选项只提供该字段的一个 value。一个选项不得捆绑修改多个字段；后端会生成选项 ID 和冻结更新，你不得生成系统主键。
4.1 同一建议组内的 value 必须互不相同，且不得等于 current_requirement_draft（存在时）或 active_requirement 中该字段的当前值。
4.2 只有 selection_scope 非空且最新用户消息正在选择其中选项时，才能返回 proposal_selections；只能引用 selection_scope 中真实存在的 proposal_id、suggestion_set_id 和 option_id。selection_scope 为空时 proposal_selections 必须为空。不得改写冻结选项值，也不得把选择复制进 explicit_updates。无法唯一确定时提出 clarifying_question，不猜测。
4.3 用户消息的 reply_to 精确决定 selection_scope。范围内只有一个建议组且用户表达可唯一对应某个选项时，必须直接返回精确 ID，不得再次询问是哪一组。proposal_history 只用于理解上下文，绝不能作为选择 ID 来源或重复提交已有选择。
5. 已能直接回答或给选项时不得用问题代替答案；每轮最多一个 clarifying_question。
6. 不得编造项目、附件、费用或生产状态，不得选择供应商、模型、工作流或预算，不得承诺已生成素材。
7. 不得输出风险等级、Markdown 代码块、解释文字或 JSON 之外的内容。
8. 当最新用户消息的语义是在请求比较、推荐或多个可选创作方向时，必须返回 suggestion_sets；不要依赖固定关键词匹配，也不能用 clarifying_question 代替可直接给出的选项。
9. 用户询问“内容方向”时，选项标题、说明和值都只能描述叙事、结构、内容目标、表达重点和观看感受；不得出现具体景别、机位、镜头运动、镜头切换、转场、剪辑手法、特效、计时器、模型、工作流或生产参数，这些属于后续智能体。即使用户提到这些执行方式，创作制片人也只登记为明确限制，不替后续智能体展开方案。
10. 除 duration_seconds、aspect_ratio、audio_mode 等合同枚举外，建议值必须使用普通用户可读的简洁中文描述，不返回 snake_case、内部代码或英文机器键。
11. active_requirement 和 confirmed_decisions 是当前已生效基线；用户可以明确提出修改，并由 explicit_updates 形成待确认候选。若用户没有明确修改，audio_mode=off 时自然回复、选项说明和字段更新都不得建议音乐、旁白、对白、TTS 或对口型。画面文字属于独立创作选择，用户未明确要求时不得自行加入字幕、标题或文字动画。
12. confirmed_attachment_bindings 中 content_access=metadata_only 的附件只提供文件事实，不代表你看过画面、听过声音或理解过媒体内容；需要内容信息时应明确请用户描述，不得编造。
13. explicit_updates 或 proposal_selections 只会生成待用户确认的草稿修订，不会直接修改 active_requirement。assistant_reply 不得声称配置已经确认或生效。
14. 当 runtime_context.turn_intent=initial_guidance 时，根据 active_requirement 的主题主动给出方向。必须只返回 1 个 suggestion_set，field_key 必须为 creative_direction，其中提供 2 到 3 个整体创作方向；每个 option.value 必须与该 option.label 完全相同，使用简洁中文短语，不能把详细方案藏进冻结值。label、summary 和 value 都只描述内容重点、叙事取向和观看感受，不得描述声音、字幕、景别、机位、镜头运动、剪辑、转场、特效或生产方式。suggestion_sets.source_message_ids 引用本轮 system 初始化消息 ID，explicit_updates 和 proposal_selections 必须为空，不得把系统引导写成用户事实。
15. 当 current_requirement_draft 非空时，它是本轮唯一草稿基线；新回复与建议必须在其已有字段之上继续丰富，不得退回 active_requirement 丢失草稿内容。
16. 每轮必须先形成 creative_diagnosis。project_type 是内容用途判断，不是模板或生产路由；stage 只是本轮创作判断，不代表系统状态或正式需求已就绪。established_fields 只列出当前输入中已有明确依据的字段；open_gaps 只列出仍会显著影响创作方向的字段，二者不得重复。
17. focus_field 是本轮唯一优先讨论维度，必须出现在 open_gaps 中；stage=ready_to_confirm 时可以为 null。focus_reason 要解释该维度为何比其他缺口更值得先讨论，不能只写“信息不足”。存在建议组时，至少一组的 field_key 必须等于 focus_field；使用 clarifying_question 时也必须围绕 focus_field。不得依靠主题关键词、固定问卷顺序或项目类型硬编码决定焦点，应结合已确认字段、草稿和整段会话判断。
18. 除精确选择、用户只要求解释一个问题或 stage=ready_to_confirm 外，每轮应围绕 focus_field 主动给出 2 到 3 个明显不同的可选方向，让用户可以继续讨论而不是只收到“已记录”。诊断只用于解释引导，不能进入 explicit_updates，也不能声称已经修改或确认需求。
19. 当 runtime_context.turn_intent=selection_followup 时，结构化点击已经由系统按冻结 Option 保存。你不得重新选择、解释或登记该值，proposal_selections 和 explicit_updates 必须为空。先自然确认用户刚才的选择，再读取 current_requirement_draft 重新诊断；stage 不是 ready_to_confirm 时必须聚焦另一个仍重要的缺口并给出 2 到 3 个选项，不得重复 runtime_context.selection_followup.selected_field_keys。stage=ready_to_confirm 时应总结现有需求，不强制生成建议组。
"""


class ConfiguredCreativeAgentGateway:
    def __init__(
        self,
        *,
        transport: AgentChatTransport | None = None,
        credential_resolver: EnvironmentCredentialResolver | None = None,
    ) -> None:
        from ..providers.credentials import EnvironmentCredentialResolver

        self.transport = transport or HttpxAgentChatTransport()
        self.credential_resolver = credential_resolver or EnvironmentCredentialResolver.from_environment()

    def select(self, session: Session) -> CreativeAgentSelection:
        rows = list(session.execute(
            select(ModelConfigVersion, ProviderConfigVersion, ProductionConfigVersion)
            .join(ProviderConfigVersion, ProviderConfigVersion.id == ModelConfigVersion.provider_config_version_id)
            .join(ProductionConfigVersion, ProductionConfigVersion.id == ModelConfigVersion.production_config_version_id)
            .where(
                ModelConfigVersion.agent_role == "creative",
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
            raise AgentGatewayError("CREATIVE_MODEL_NOT_CONFIGURED", "当前没有已发布的创作模型配置。")
        if len(latest_by_key) != 1:
            raise AgentGatewayError("CREATIVE_MODEL_SELECTION_AMBIGUOUS", "当前存在多个创作模型系列，必须先在系统配置中保留一个明确选择。")
        model, provider, config = next(iter(latest_by_key.values()))
        configured_contracts = (
            model.input_contract_version,
            model.output_schema_version,
            model.prompt_contract_version,
        )
        expected_contracts = (
            CREATIVE_INPUT_CONTRACT_VERSION,
            CREATIVE_OUTPUT_SCHEMA_VERSION,
            CREATIVE_PROMPT_CONTRACT_VERSION,
        )
        if configured_contracts != expected_contracts:
            raise AgentGatewayError(
                "CREATIVE_MODEL_CONTRACT_MISMATCH",
                "已发布创作模型配置的输入、输出或 Prompt 合同版本与当前运行代码不一致，请先发布匹配的系统配置版本。",
            )
        if provider.adapter_kind != "openai_compatible":
            raise AgentGatewayError("CREATIVE_MODEL_ADAPTER_UNSUPPORTED", "当前创作模型没有绑定 OpenAI-compatible 服务供应商。")
        if "text_generation" not in provider.capabilities:
            raise AgentGatewayError("CREATIVE_MODEL_CAPABILITY_MISSING", "当前创作模型供应商未声明文本生成能力。")
        return CreativeAgentSelection(
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
        selection: CreativeAgentSelection,
        manifest_payload: dict[str, Any],
    ) -> CreativeAgentResult:
        if os.getenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
            raise AgentGatewayError("AGENT_MODEL_EXECUTION_DISABLED", "创作模型真实调用尚未获得后端执行授权。")
        credential = self.credential_resolver.resolve(selection.credential_ref)
        if not credential.available or credential.secret is None:
            raise AgentGatewayError("AGENT_MODEL_CREDENTIAL_UNAVAILABLE", f"创作模型后端凭据不可用（{credential.state}）。")
        context_payload = {
            key: value for key, value in manifest_payload.items() if key != "conversation"
        }
        proposal_history = manifest_payload["conversation"].get("proposal_history", [])
        history_by_assistant_message = {
            item["assistant_message_id"]: item for item in proposal_history
        }
        selection_scope = manifest_payload["conversation"].get("selection_scope")
        chat_messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "以下是当前项目的结构化权威上下文，不是新的用户需求："
                + json.dumps(context_payload, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        for message in manifest_payload["conversation"]["messages"]:
            content = f"[message_id={message['id']}]\n{message['content']}"
            history = history_by_assistant_message.get(message["id"])
            if message["role"] == "assistant" and history:
                content += "\n[proposal_history=" + json.dumps(
                    history,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "]"
            if (
                message["role"] == "assistant"
                and selection_scope
                and selection_scope.get("assistant_message_id") == message["id"]
            ):
                content += "\n[selection_scope=" + json.dumps(
                    selection_scope,
                    ensure_ascii=False,
                    separators=(",", ":"),
                ) + "]"
            chat_messages.append({
                "role": message["role"],
                "content": content,
            })
        if manifest_payload["runtime_context"].get("turn_intent") == "initial_guidance":
            initialization_message = next(
                item for item in reversed(manifest_payload["conversation"]["messages"])
                if item["role"] == "system"
            )
            chat_messages.append({
                "role": "system",
                "content": (
                    "本轮首次引导的结构合同：只返回一个 creative_direction 建议组；"
                    "该组 source_message_ids 必须精确为 [\""
                    + initialization_message["id"]
                    + "\"]；每个 option.value 必须与 option.label 完全相同；"
                    "explicit_updates 与 proposal_selections 必须为空。"
                ),
            })
        payload: dict[str, Any] = {
            "model": selection.provider_model_id,
            "messages": chat_messages,
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
            raise AgentGatewayError("AGENT_MODEL_OUTPUT_SCHEMA_INVALID", "创作模型输出不是有效 JSON 对象。") from exc
        try:
            output = CreativeAgentOutput.model_validate(parsed)
        except ValidationError as exc:
            raise AgentGatewayError(
                "AGENT_MODEL_OUTPUT_SCHEMA_INVALID",
                "创作模型输出不符合严格 V2 对话合同。",
                raw_output=parsed if isinstance(parsed, dict) else None,
                diagnostics=exc.errors(include_input=False),
            ) from exc
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        request_id = str(response.get("id") or "").strip() or None
        return CreativeAgentResult(output, parsed, request_id, usage)


class DeterministicCreativeAgentGateway:
    """Explicit test gateway; never registered by the runtime application."""

    def select(self, session: Session) -> CreativeAgentSelection:
        return CreativeAgentSelection(
            production_config_version_id="v2.creation.test.v1",
            model_config_version_id="model_config_test_creative",
            provider_config_version_id="provider_config_test_mock",
            model_provider="mock",
            model_name="deterministic-creative-v1",
            provider_model_id="deterministic-creative-v1",
            base_url="https://example.invalid/v1",
            credential_ref=None,
            timeout_seconds=1,
            input_contract_version=CREATIVE_INPUT_CONTRACT_VERSION,
            prompt_contract_version=CREATIVE_PROMPT_CONTRACT_VERSION,
            output_schema_version=CREATIVE_OUTPUT_SCHEMA_VERSION,
            max_output_tokens=None,
            sampling={},
        )

    def invoke(self, selection: CreativeAgentSelection, manifest_payload: dict[str, Any]) -> CreativeAgentResult:
        if manifest_payload["runtime_context"].get("turn_intent") == "initial_guidance":
            source = manifest_payload["conversation"]["messages"][-1]
            output = CreativeAgentOutput(
                assistant_reply="我先根据主题给你三个可继续讨论的方向。选一个作为起点，也可以直接告诉我你想混合或修改哪些部分。",
                creative_diagnosis=CreativeDiagnosis(
                    project_type="personal_record",
                    stage="exploring",
                    summary="目前只有主题和基础交付信息，需要先确定整体内容取向。",
                    established_fields=["title", "core_topic", "duration_seconds", "aspect_ratio", "audio_mode"],
                    open_gaps=[CreativeGap(field_key="creative_direction", reason="整体取向会决定后续讨论受众、目标和表达方式。")],
                    focus_field="creative_direction",
                    focus_reason="先选定整体方向，后续补充才不会变成互不关联的字段清单。",
                    source_message_ids=[source["id"]],
                ),
                suggestion_sets=[CreativeSuggestionSet(
                    category="creative_direction",
                    title="这次内容先从哪个方向展开？",
                    field_key="creative_direction",
                    source_message_ids=[source["id"]],
                    options=[
                        CreativeSuggestionOption(label="真实记录", summary="突出过程感和真实体验。", value="真实记录"),
                        CreativeSuggestionOption(label="挑战成长", summary="围绕目标、困难和结果形成变化。", value="挑战成长"),
                        CreativeSuggestionOption(label="实用分享", summary="兼顾体验与可带走的方法。", value="实用分享"),
                    ],
                )],
            )
            return CreativeAgentResult(output, output.model_dump(mode="json"), "test-initial-request", {"total_tokens": 1})
        if manifest_payload["runtime_context"].get("turn_intent") == "selection_followup":
            latest = next(
                item for item in reversed(manifest_payload["conversation"]["messages"])
                if item["role"] == "user"
            )
            selected_fields = set(
                manifest_payload["runtime_context"].get("selection_followup", {}).get("selected_field_keys", [])
            )
            focus_field = "target_audience" if "content_structure" in selected_fields else "content_structure"
            focus_title = "这次内容主要希望给谁看？" if focus_field == "target_audience" else "你希望内容怎样展开？"
            focus_options = (
                [
                    CreativeSuggestionOption(label="同好人群", summary="面向已经关注这一主题的人。", value="同好人群"),
                    CreativeSuggestionOption(label="普通观众", summary="让不了解背景的人也容易进入。", value="普通观众"),
                    CreativeSuggestionOption(label="潜在参与者", summary="重点回应想要尝试这件事的人。", value="潜在参与者"),
                ]
                if focus_field == "target_audience" else [
                    CreativeSuggestionOption(label="过程记录", summary="按事情发生的过程自然推进。", value="过程记录"),
                    CreativeSuggestionOption(label="挑战成长", summary="突出目标、困难和最终变化。", value="挑战成长"),
                    CreativeSuggestionOption(label="经验分享", summary="围绕体验提炼可带走的方法。", value="经验分享"),
                ]
            )
            output = CreativeAgentOutput(
                assistant_reply="这个方向已经记入当前草稿。接下来我们把内容如何展开定下来。",
                creative_diagnosis=CreativeDiagnosis(
                    project_type="personal_record",
                    stage="shaping",
                    summary="整体创作方向已经明确，下一步需要确定内容的组织方式。",
                    established_fields=["title", "core_topic", "creative_direction"],
                    open_gaps=[CreativeGap(field_key=focus_field, reason="这个维度会显著影响后续内容表达。")],
                    focus_field=focus_field,
                    focus_reason="方向确定后，先明确内容结构最能帮助后续继续补充受众和表达语气。",
                    source_message_ids=[latest["id"]],
                ),
                suggestion_sets=[CreativeSuggestionSet(
                    category="content_direction",
                    title=focus_title,
                    field_key=focus_field,
                    source_message_ids=[latest["id"]],
                    options=focus_options,
                )],
            )
            return CreativeAgentResult(output, output.model_dump(mode="json"), "test-selection-followup", {"total_tokens": 1})
        latest = next(
            item for item in reversed(manifest_payload["conversation"]["messages"])
            if item["role"] == "user"
        )
        output = CreativeAgentOutput(
            assistant_reply=f"已收到：{latest['content']}",
            creative_diagnosis=CreativeDiagnosis(
                project_type="personal_record",
                stage="shaping",
                summary="创作方向已经开始形成，下一步需要明确内容如何展开。",
                established_fields=["title", "core_topic", "creative_direction"],
                open_gaps=[CreativeGap(field_key="content_structure", reason="结构决定有限时长内如何组织内容重点。")],
                focus_field="content_structure",
                focus_reason="先确定内容结构，才能继续判断受众和语气是否匹配。",
                source_message_ids=[latest["id"]],
            ),
            suggestion_sets=[CreativeSuggestionSet(
                category="content_direction",
                title="你希望采用哪种内容结构？",
                field_key="content_structure",
                source_message_ids=[latest["id"]],
                options=[
                    CreativeSuggestionOption(
                        label="训练日记",
                        summary="按准备、训练和完成三个阶段自然推进。",
                        value="训练日记",
                    ),
                    CreativeSuggestionOption(
                        label="挑战记录",
                        summary="用明确目标和训练结果形成前后对比。",
                        value="挑战记录",
                    ),
                    CreativeSuggestionOption(
                        label="技巧教学",
                        summary="围绕动作讲解和训练要点组织内容。",
                        value="技巧教学",
                    ),
                ],
            )],
            explicit_updates=[ProposedFieldUpdate(
                field_key="creative_direction",
                value=latest["content"],
                source_message_ids=[latest["id"]],
            )],
            clarifying_question=None,
        )
        return CreativeAgentResult(output, output.model_dump(mode="json"), "test-request", {"total_tokens": 1})


def get_creative_agent_gateway() -> CreativeAgentGateway:
    return ConfiguredCreativeAgentGateway()
