from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal, Protocol
from urllib.parse import urljoin

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..creation.agent_gateway import AgentChatTransport, AgentGatewayError, HttpxAgentChatTransport
from ..core.config import RUNTIME_ROOT
from ..db.models import ModelConfigVersion, ProductionConfigVersion, ProviderConfigVersion


class QCEvidenceOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["asset", "image_region"]
    asset_id: str = Field(min_length=1, max_length=48)
    region: list[float] | None = Field(default=None, min_length=4, max_length=4)


class QCFindingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{2,79}$")
    category: Literal[
        "identity", "continuity", "semantic_match", "composition", "visible_text", "motion", "audio_content"
    ]
    severity: Literal["low", "medium", "high"]
    confidence: float = Field(ge=0, le=1)
    summary: str = Field(min_length=1, max_length=500)
    evidence: list[QCEvidenceOutput] = Field(min_length=1, max_length=20)
    contract_refs: list[str] = Field(min_length=1, max_length=20)
    suggested_review_action: Literal[
        "inspect_asset", "compare_reference", "check_continuity", "check_text", "check_motion", "check_audio"
    ]


class QCOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overall_recommendation: Literal["review_required"]
    findings: list[QCFindingOutput] = Field(max_length=50)
    analyzer_version: Literal["visual-qc.v1"]


@dataclass(frozen=True)
class QCSelection:
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
    provider_capabilities: tuple[str, ...]


@dataclass(frozen=True)
class QCResult:
    output: QCOutput
    raw_output: dict[str, Any]
    provider_request_id: str | None
    token_usage: dict[str, Any]


class QCGateway(Protocol):
    def select(self, session: Session) -> QCSelection: ...

    def invoke(self, selection: QCSelection, manifest_payload: dict[str, Any], media_path: Path) -> QCResult: ...


_QC_SYSTEM_PROMPT = """你是片场 V2 的质量审核智能体。你只分析已通过文件合同检查的单个素材，不批准、拒绝、重试或修改素材。
必须只返回一个 JSON 对象，严格符合：
{"overall_recommendation":"review_required","findings":[{"finding_code":"SEMANTIC_MISMATCH","category":"identity|continuity|semantic_match|composition|visible_text|motion|audio_content","severity":"low|medium|high","confidence":0.8,"summary":"简明问题说明","evidence":[{"kind":"asset|image_region","asset_id":"逐字复制输入素材 ID","region":[0.1,0.1,0.8,0.8]}],"contract_refs":["逐字复制输入 contract_reference_catalog 中的值"],"suggested_review_action":"inspect_asset|compare_reference|check_continuity|check_text|check_motion|check_audio"}],"analyzer_version":"visual-qc.v1"}
竖线分隔的是允许枚举，实际输出只能选择一个值。
规则：
1. 只报告能由当前素材直接观察且能指向冻结合同的问题；没有可靠发现时 findings 返回空数组。
2. 每条发现必须至少包含一个当前素材证据和一个白名单合同引用，不得创建、缩写或猜测 ID。
3. face_visibility=not_visible 时不得报告缺少正脸；只有 required 时才检查正脸要求。
4. OCR、身份、连续性、语义和构图判断都只是人工审核建议，不得输出 passed、approved、rejected 或 blocked。
5. 不得提出重试、换模型、换工作流、改写提示词或自动修复方案。
6. 当前输入是单张图片，不得声称已经检查视频动态、音频内容或未提供的其他镜头。
7. 不得输出 Markdown、解释文字或 JSON 之外的内容。
8. production_profile 是用户已确认的生产方式证据。three_frame 模式下只能结合当前单帧的 frame_role 与冻结镜头合同报告可观察问题；不得声称仅凭一张图片已经验证完整首中尾连续性。
"""


class ConfiguredQCGateway:
    def __init__(self, *, transport: AgentChatTransport | None = None) -> None:
        self.transport = transport or HttpxAgentChatTransport()

    def select(self, session: Session) -> QCSelection:
        rows = list(session.execute(
            select(ModelConfigVersion, ProviderConfigVersion, ProductionConfigVersion)
            .join(ProviderConfigVersion, ProviderConfigVersion.id == ModelConfigVersion.provider_config_version_id)
            .join(ProductionConfigVersion, ProductionConfigVersion.id == ModelConfigVersion.production_config_version_id)
            .where(
                ModelConfigVersion.agent_role == "qc",
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
            raise AgentGatewayError("QC_MODEL_NOT_CONFIGURED", "当前没有已发布的质量审核模型配置。")
        if len(latest_by_key) != 1:
            raise AgentGatewayError("QC_MODEL_SELECTION_AMBIGUOUS", "当前存在多个质量审核模型系列，请保留一个明确选择。")
        model, provider, config = next(iter(latest_by_key.values()))
        if provider.adapter_kind != "openai_compatible":
            raise AgentGatewayError("QC_ADAPTER_UNSUPPORTED", "质量审核模型没有绑定 OpenAI-compatible 服务供应商。")
        if "vision_analysis" not in provider.capabilities:
            raise AgentGatewayError("QC_VISION_CAPABILITY_MISSING", "质量审核模型供应商未声明图片理解能力。")
        expected = {
            "input_contract_version": "qc-agent-input.v2",
            "output_schema_version": "qc-report-candidate.v1",
            "prompt_contract_version": "qc-agent-prompt.v2",
        }
        if {key: getattr(model, key) for key in expected} != expected:
            raise AgentGatewayError("QC_CONTRACT_VERSION_UNSUPPORTED", "质量审核模型配置的合同版本与当前运行代码不一致。")
        return QCSelection(
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
            provider_capabilities=tuple(provider.capabilities or []),
        )

    def invoke(self, selection: QCSelection, manifest_payload: dict[str, Any], media_path: Path) -> QCResult:
        if os.getenv("V2_AGENT_MODEL_EXECUTION_ENABLED", "").strip().lower() not in {"1", "true", "yes"}:
            raise AgentGatewayError("AGENT_MODEL_EXECUTION_DISABLED", "质量审核模型真实调用尚未获得后端执行授权。")
        media = manifest_payload["asset"]
        if media["asset_type"] != "image" or not str(media["mime_type"]).startswith("image/"):
            raise AgentGatewayError("QC_MEDIA_ANALYSIS_UNSUPPORTED", "当前质量审核模型合同只支持单张图片；视频与音频继续由人工审核。")
        api_key = str(selection.api_key or "").strip()
        if not api_key:
            raise AgentGatewayError("AGENT_MODEL_CREDENTIAL_UNAVAILABLE", "质量审核模型供应商的 API Key 未填写。")
        encoded = base64.b64encode(media_path.read_bytes()).decode("ascii")
        user_content: list[dict[str, Any]] = [
            {"type": "text", "text": "以下是不可变质量审核输入合同：" + json.dumps(manifest_payload, ensure_ascii=False, separators=(",", ":")) + "\n第一张图片是待审素材。"},
            {"type": "image_url", "image_url": {"url": f"data:{media['mime_type']};base64,{encoded}"}},
        ]
        for reference in manifest_payload.get("entity_reference_images", []):
            uri = str(reference.get("uri") or "")
            prefix = "runtime://attachments/"
            if not uri.startswith(prefix):
                raise AgentGatewayError("QC_REFERENCE_URI_INVALID", "质量审核主参考图片地址无效。")
            relative = PurePosixPath(uri[len(prefix):])
            root = RUNTIME_ROOT.resolve()
            path = root.joinpath(*relative.parts).resolve()
            if relative.is_absolute() or ".." in relative.parts or not path.is_relative_to(root) or not path.is_file():
                raise AgentGatewayError("QC_REFERENCE_FILE_MISSING", "质量审核主参考图片文件不存在。")
            reference_encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            user_content.extend([
                {"type": "text", "text": f"主参考图片，附件 ID：{reference['attachment_id']}，实体版本 ID：{reference['entity_version_id']}。"},
                {"type": "image_url", "image_url": {"url": f"data:{reference['mime_type']};base64,{reference_encoded}"}},
            ])
        payload: dict[str, Any] = {
            "model": selection.provider_model_id,
            "messages": [
                {"role": "system", "content": _QC_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
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
        try:
            parsed = json.loads(response["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AgentGatewayError("QC_OUTPUT_SCHEMA_INVALID", "质量审核输出不是有效 JSON 对象。") from exc
        try:
            output = QCOutput.model_validate(parsed)
            validate_qc_output_against_manifest(output, manifest_payload)
        except ValidationError as exc:
            raise AgentGatewayError("QC_OUTPUT_SCHEMA_INVALID", "质量审核输出不符合严格候选合同。", raw_output=parsed if isinstance(parsed, dict) else None, diagnostics=exc.errors(include_input=False)) from exc
        except ValueError as exc:
            raise AgentGatewayError("QC_OUTPUT_CONTRACT_INVALID", str(exc), raw_output=parsed if isinstance(parsed, dict) else None) from exc
        usage = response.get("usage") if isinstance(response.get("usage"), dict) else {}
        return QCResult(output, parsed, str(response.get("id") or "").strip() or None, usage)


def validate_qc_output_against_manifest(output: QCOutput, manifest: dict[str, Any]) -> None:
    asset_id = manifest["asset"]["id"]
    allowed_refs = set(manifest["contract_reference_catalog"])
    face_visibility = manifest["shot_contract"].get("face_visibility")
    for finding in output.findings:
        if any(item.asset_id != asset_id for item in finding.evidence):
            raise ValueError("质量发现只能引用当前输入素材。")
        unknown = sorted(set(finding.contract_refs) - allowed_refs)
        if unknown:
            raise ValueError(f"质量发现引用了不在白名单中的合同字段：{unknown}。")
        if face_visibility == "not_visible" and finding.category == "identity" and finding.finding_code in {"FACE_MISSING", "FACE_NOT_VISIBLE"}:
            raise ValueError("不要求露脸的镜头不得报告正脸缺失。")


class DeterministicQCGateway:
    """Explicit test gateway; never registered by the runtime application."""

    def select(self, session: Session) -> QCSelection:
        config_id = session.scalar(
            select(ProductionConfigVersion.id)
            .where(ProductionConfigVersion.status == "published")
            .order_by(ProductionConfigVersion.version_number.desc())
            .limit(1)
        ) or "v2.qc.test.v1"
        return QCSelection(
            config_id, "model_config_test_qc", "provider_config_test_mock", "mock", "deterministic-qc-v1",
            "deterministic-qc-v1", "https://example.invalid/v1", None, 1, "qc-agent-input.v2", "qc-agent-prompt.v2",
            "qc-report-candidate.v1", None, {}, ("vision_analysis",),
        )

    def invoke(self, selection: QCSelection, manifest_payload: dict[str, Any], media_path: Path) -> QCResult:
        output = QCOutput(overall_recommendation="review_required", findings=[], analyzer_version="visual-qc.v1")
        return QCResult(output, output.model_dump(mode="json"), "test-qc-request", {"total_tokens": 1})


def get_qc_gateway() -> QCGateway:
    return ConfiguredQCGateway()
