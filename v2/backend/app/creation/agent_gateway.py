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
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class FieldUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_key: Literal[
        "title",
        "core_topic",
        "duration_seconds",
        "aspect_ratio",
        "audio_mode",
        "creative_direction",
    ]
    value: Any
    source_message_id: str = Field(min_length=1, max_length=48)
    risk_level: Literal["low", "medium", "high"]


class CreativeAgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assistant_reply: str = Field(min_length=1, max_length=8000)
    field_updates: list[FieldUpdate] = Field(default_factory=list, max_length=32)


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


_SYSTEM_PROMPT = """你是片场 V2 的创作需求智能体。你只负责回复用户并提出结构化需求候选，不执行生产。
必须只返回一个 JSON 对象，严格符合：
{"assistant_reply":"中文回复","field_updates":[{"field_key":"允许字段","value":"值","source_message_id":"消息ID","risk_level":"low|medium|high"}]}
规则：
1. 只依据输入清单中的用户消息、当前需求、已确认决策和附件绑定；不得编造未提供的事实。
2. 只有用户明确表达了字段值时才输出 field_updates；普通问候可以返回空数组。
3. source_message_id 必须来自输入清单；不得改写 ID。
4. 不得确认中高风险决策，不得选择供应商、工作流或预算，不得承诺已生成素材。
5. 不得输出 Markdown 代码块、解释文字或 JSON 之外的内容。
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
        payload: dict[str, Any] = {
            "model": selection.provider_model_id,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(manifest_payload, ensure_ascii=False, separators=(",", ":"))},
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
            output = CreativeAgentOutput.model_validate(parsed)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValidationError) as exc:
            raise AgentGatewayError("AGENT_MODEL_OUTPUT_SCHEMA_INVALID", "创作模型输出不符合严格对话合同。") from exc
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
            prompt_contract_version="creative.v1",
            output_schema_version="requirement-candidate.v1",
            max_output_tokens=None,
            sampling={},
        )

    def invoke(self, selection: CreativeAgentSelection, manifest_payload: dict[str, Any]) -> CreativeAgentResult:
        latest = manifest_payload["messages"][-1]
        output = CreativeAgentOutput(
            assistant_reply=f"已收到：{latest['content']}",
            field_updates=[FieldUpdate(
                field_key="creative_direction",
                value=latest["content"],
                source_message_id=latest["id"],
                risk_level="medium",
            )],
        )
        return CreativeAgentResult(output, output.model_dump(mode="json"), "test-request", {"total_tokens": 1})


def get_creative_agent_gateway() -> CreativeAgentGateway:
    return ConfiguredCreativeAgentGateway()
