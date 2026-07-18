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


class CreativeAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_reply: str = Field(min_length=1, max_length=8000)
    suggestion_sets: list[CreativeSuggestionSet] = Field(default_factory=list, max_length=3)
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


_SYSTEM_PROMPT = """你是片场 V2 的创作制片人。你负责自然对话、理解需求、主动提出创意选择和登记用户明确表达，不执行脚本策划、分镜或生产。
必须只返回一个 JSON 对象，严格符合：
{"assistant_reply":"中文回复","suggestion_sets":[{"category":"类别","title":"问题","options":[{"label":"选项名","summary":"差异说明","proposed_updates":[{"field_key":"允许字段","value":"建议值","source_message_ids":["用户消息ID"]}]}]}],"explicit_updates":[{"field_key":"允许字段","value":"用户明确值","source_message_ids":["用户消息ID"]}],"clarifying_question":null}
允许字段及用途：
- title 项目名称；core_topic 核心主题；content_goal 内容目标；platform 发布平台；target_audience 目标受众。
- duration_seconds 目标秒数；aspect_ratio 画幅；audio_mode 仅 off 或 voiceover。
- visual_style 视觉风格；tone 情绪语气；content_structure 内容结构；call_to_action 结尾行动号召。
- creative_direction 整体创作方向；creative_constraints 用户明确限制的字符串列表。
合法建议示例：
{"category":"content_direction","title":"你希望采用哪种内容结构？","options":[{"label":"训练日记","summary":"按准备、训练、完成推进","proposed_updates":[{"field_key":"content_structure","value":"training_diary","source_message_ids":["最新用户消息ID"]}]},{"label":"挑战记录","summary":"突出目标和结果对比","proposed_updates":[{"field_key":"content_structure","value":"challenge_record","source_message_ids":["最新用户消息ID"]}]}]}
规则：
1. 读取 conversation 中按顺序提供的用户和助手消息，理解“第一个”“刚才那个”等上下文指代；助手消息不是用户事实。
2. 只有用户明确表达的值才能进入 explicit_updates，且 source_message_ids 只能引用 role=user 的消息。
3. 用户要求建议时直接给 2 到 3 个互斥且有明显差异的选项，推荐项放第一；建议不能进入 explicit_updates。
4. suggestion_sets 每组只能有 2 到 3 个选项；后端会生成选项 ID，你不得生成系统主键。
4.1 每个可点击选项的 proposed_updates 必须至少包含一项，并使用上面的允许字段；不能返回空数组。
5. 已能直接回答或给选项时不得用问题代替答案；每轮最多一个 clarifying_question。
6. 不得编造项目、附件、费用或生产状态，不得选择供应商、模型、工作流或预算，不得承诺已生成素材。
7. 不得输出风险等级、Markdown 代码块、解释文字或 JSON 之外的内容。
8. 最新用户消息出现“给我选项、几个方向、推荐、怎么选、方案”等明确请求时，必须返回 suggestion_sets，不能回复没理解，也不能用 clarifying_question 反问。
9. 用户询问“内容方向”时应围绕叙事、结构、钩子或表达重点提供选择；除非用户提到人物出镜，否则不要把是否出镜当作内容方向。
10. 除 duration_seconds、aspect_ratio、audio_mode 等合同枚举外，建议值必须使用普通用户可读的简洁中文描述，不返回 snake_case、内部代码或英文机器键。
11. 必须遵守 active_requirement 和 confirmed_decisions；audio_mode=off 时，自然回复、选项说明和字段更新都不得建议音乐、旁白、对白、TTS 或对口型，也不得自行用字幕替代音频。
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
        chat_messages: list[dict[str, str]] = [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {
                "role": "user",
                "content": "以下是当前项目的结构化权威上下文，不是新的用户需求："
                + json.dumps(context_payload, ensure_ascii=False, separators=(",", ":")),
            },
        ]
        for message in manifest_payload["conversation"]["messages"]:
            chat_messages.append({
                "role": message["role"],
                "content": f"[message_id={message['id']}]\n{message['content']}",
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
            prompt_contract_version="creative-dialogue.v2",
            output_schema_version="creative-turn.v2",
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
