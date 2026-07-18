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


class ProposedFieldUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_key: CREATIVE_FIELD_KEYS
    value: Any
    source_message_ids: list[str] = Field(min_length=1, max_length=8)


class CreativeSuggestionOption(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=1, max_length=60)
    summary: str = Field(min_length=1, max_length=240)
    proposed_updates: list[ProposedFieldUpdate] = Field(min_length=1, max_length=8)


class CreativeSuggestionSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str = Field(min_length=1, max_length=60)
    title: str = Field(min_length=1, max_length=120)
    options: list[CreativeSuggestionOption] = Field(min_length=2, max_length=3)


class CreativeProposalSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    proposal_id: str = Field(min_length=1, max_length=48)
    suggestion_set_id: str = Field(min_length=1, max_length=48)
    option_id: str = Field(min_length=1, max_length=48)
    source_message_ids: list[str] = Field(min_length=1, max_length=8)


class CreativeAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_reply: str = Field(min_length=1, max_length=8000)
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


CREATIVE_INPUT_CONTRACT_VERSION = "v2.creative-dialogue-input.v4"
CREATIVE_OUTPUT_SCHEMA_VERSION = "v2.creative-dialogue-output.v3"
CREATIVE_PROMPT_CONTRACT_VERSION = "v2.creative-dialogue-prompt.v6"


_SYSTEM_PROMPT = """你是片场 V2 的创作制片人。你负责自然对话、理解需求、主动提出创意选择和登记用户明确表达，不执行脚本策划、分镜或生产。
必须只返回一个 JSON 对象，严格符合：
{"assistant_reply":"中文回复","suggestion_sets":[{"category":"类别","title":"问题","options":[{"label":"选项名","summary":"差异说明","proposed_updates":[{"field_key":"允许字段","value":"建议值","source_message_ids":["用户消息ID"]}]}]}],"proposal_selections":[{"proposal_id":"已有提案ID","suggestion_set_id":"已有建议组ID","option_id":"已有选项ID","source_message_ids":["用户选择消息ID"]}],"explicit_updates":[{"field_key":"允许字段","value":"用户明确值","source_message_ids":["用户消息ID"]}],"clarifying_question":null}
允许字段及用途：
- title 项目名称；core_topic 核心主题；content_goal 内容目标；platform 发布平台；target_audience 目标受众。
- duration_seconds 目标秒数；aspect_ratio 画幅；audio_mode 仅 off 或 voiceover。
- visual_style 视觉风格；tone 情绪语气；content_structure 内容结构；call_to_action 结尾行动号召。
- creative_direction 整体创作方向；creative_constraints 用户明确限制的字符串列表。
合法建议示例：
{"category":"content_direction","title":"你希望采用哪种内容结构？","options":[{"label":"训练日记","summary":"按准备、训练、完成推进","proposed_updates":[{"field_key":"content_structure","value":"training_diary","source_message_ids":["最新用户消息ID"]}]},{"label":"挑战记录","summary":"突出目标和结果对比","proposed_updates":[{"field_key":"content_structure","value":"challenge_record","source_message_ids":["最新用户消息ID"]}]}]}
规则：
1. 读取 conversation 中按顺序提供的用户和助手消息；proposal_history 是不含系统 ID 的只读历史，助手消息和未选择建议都不是用户事实，history.selections 才表示用户曾经作出的选择。
2. 只有用户明确表达的值才能进入 explicit_updates，且 source_message_ids 只能引用 role=user 的消息。
3. 用户要求建议时直接给 2 到 3 个互斥且有明显差异的选项，推荐项放第一；建议不能进入 explicit_updates。
4. suggestion_sets 每组只能有 2 到 3 个选项；后端会生成选项 ID，你不得生成系统主键。
4.1 每个可点击选项的 proposed_updates 必须至少包含一项，并使用上面的允许字段；不能返回空数组。
4.2 只有 selection_scope 非空且最新用户消息正在选择其中选项时，才能返回 proposal_selections；只能引用 selection_scope 中真实存在的 proposal_id、suggestion_set_id 和 option_id。selection_scope 为空时 proposal_selections 必须为空。不得改写冻结选项值，也不得把选择复制进 explicit_updates。无法唯一确定时提出 clarifying_question，不猜测。
4.3 用户消息的 reply_to 精确决定 selection_scope。范围内只有一个建议组且用户表达可唯一对应某个选项时，必须直接返回精确 ID，不得再次询问是哪一组。proposal_history 只用于理解上下文，绝不能作为选择 ID 来源或重复提交已有选择。
5. 已能直接回答或给选项时不得用问题代替答案；每轮最多一个 clarifying_question。
6. 不得编造项目、附件、费用或生产状态，不得选择供应商、模型、工作流或预算，不得承诺已生成素材。
7. 不得输出风险等级、Markdown 代码块、解释文字或 JSON 之外的内容。
8. 当最新用户消息的语义是在请求比较、推荐或多个可选创作方向时，必须返回 suggestion_sets；不要依赖固定关键词匹配，也不能用 clarifying_question 代替可直接给出的选项。
9. 用户询问“内容方向”时应围绕叙事、结构、钩子、表达重点和观看感受提供选择；不要设计具体镜头、慢动作、分屏、剪辑、计时器、模型、工作流或生产参数，这些属于后续智能体。
10. 除 duration_seconds、aspect_ratio、audio_mode 等合同枚举外，建议值必须使用普通用户可读的简洁中文描述，不返回 snake_case、内部代码或英文机器键。
11. active_requirement 和 confirmed_decisions 是当前已生效基线；用户可以明确提出修改，并由 explicit_updates 形成待确认候选。若用户没有明确修改，audio_mode=off 时自然回复、选项说明和字段更新都不得建议音乐、旁白、对白、TTS 或对口型。画面文字属于独立创作选择，用户未明确要求时不得自行加入字幕、标题或文字动画。
12. confirmed_attachment_bindings 中 content_access=metadata_only 的附件只提供文件事实，不代表你看过画面、听过声音或理解过媒体内容；需要内容信息时应明确请用户描述，不得编造。
13. explicit_updates 或 proposal_selections 只会生成待用户确认的候选，不会直接修改 active_requirement。assistant_reply 必须明确表达“已整理为待确认修改”或同等含义，不得声称配置已经更新、确认或生效；没有结构化变更时也不得声称已记录为正式需求。
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
        latest = next(
            item for item in reversed(manifest_payload["conversation"]["messages"])
            if item["role"] == "user"
        )
        output = CreativeAgentOutput(
            assistant_reply=f"已收到：{latest['content']}",
            suggestion_sets=[CreativeSuggestionSet(
                category="content_direction",
                title="你希望采用哪种内容结构？",
                options=[
                    CreativeSuggestionOption(
                        label="训练日记",
                        summary="按准备、训练和完成三个阶段自然推进。",
                        proposed_updates=[ProposedFieldUpdate(
                            field_key="content_structure",
                            value="训练日记",
                            source_message_ids=[latest["id"]],
                        )],
                    ),
                    CreativeSuggestionOption(
                        label="挑战记录",
                        summary="用明确目标和训练结果形成前后对比。",
                        proposed_updates=[ProposedFieldUpdate(
                            field_key="content_structure",
                            value="挑战记录",
                            source_message_ids=[latest["id"]],
                        )],
                    ),
                    CreativeSuggestionOption(
                        label="技巧教学",
                        summary="围绕动作讲解和训练要点组织内容。",
                        proposed_updates=[ProposedFieldUpdate(
                            field_key="content_structure",
                            value="技巧教学",
                            source_message_ids=[latest["id"]],
                        )],
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
