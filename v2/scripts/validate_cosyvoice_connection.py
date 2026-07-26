from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePath, PurePosixPath
from typing import Any
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from v2.backend.app.core.config import CONNECTED_LOCAL_ASSET_ROOT_REF, RUNTIME_ROOT
from v2.backend.app.db.models import (
    AudioConfigVersion,
    ProductionConfigVersion,
    ProviderConfigVersion,
    StoragePolicyVersion,
    WorkflowSlotVersion,
)
from v2.backend.app.db.session import SessionLocal
from v2.backend.app.providers import ProviderAdapterError, ProviderExecutionRequest
from v2.backend.app.providers.cosyvoice import (
    CosyVoiceAdapter,
    CosyVoiceTransport,
    cosyvoice_workflow_contract_issues,
)
from v2.backend.app.quality.service import probe_media
from v2.backend.app.repositories import SqlAlchemyConfigurationRepository


DEFAULT_VALIDATION_TEXT = "片场 V2 配音连接验收。"


class CosyVoiceValidationError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class CosyVoiceValidationContract:
    configuration: ProductionConfigVersion
    provider: ProviderConfigVersion
    workflow: WorkflowSlotVersion
    audio: AudioConfigVersion
    storage: StoragePolicyVersion


def _external_execution_enabled() -> bool:
    return os.getenv("V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _published_configuration(
    session: Session,
    configuration_id: str | None,
) -> ProductionConfigVersion:
    statement = select(ProductionConfigVersion).where(
        ProductionConfigVersion.status == "published",
    )
    if configuration_id:
        statement = statement.where(ProductionConfigVersion.id == configuration_id)
    else:
        statement = statement.order_by(
            ProductionConfigVersion.published_at.desc(),
            ProductionConfigVersion.id.desc(),
        )
    configuration = session.scalars(statement).first()
    if configuration is None:
        raise CosyVoiceValidationError(
            "COSYVOICE_PUBLISHED_CONFIGURATION_MISSING",
            "没有找到可用于验收的已发布制作配置。",
        )
    return configuration


def load_validation_contract(
    session: Session,
    configuration_id: str | None = None,
) -> CosyVoiceValidationContract:
    configuration = _published_configuration(session, configuration_id)
    components = SqlAlchemyConfigurationRepository(session).component_rows(configuration.id)
    providers = [row for row in components["provider"] if row.adapter_kind == "cosyvoice"]
    if len(providers) != 1:
        raise CosyVoiceValidationError(
            "COSYVOICE_PROVIDER_NOT_EXACT",
            "已发布配置必须恰好包含一个 CosyVoice Provider。",
        )
    audio_rows = list(components["audio"])
    storage_rows = list(components["storage"])
    if len(audio_rows) != 1 or len(storage_rows) != 1:
        raise CosyVoiceValidationError(
            "COSYVOICE_SUPPORTING_CONTRACT_NOT_EXACT",
            "已发布配置必须恰好包含一份音频合同和一份存储合同。",
        )
    provider = providers[0]
    audio = audio_rows[0]
    storage = storage_rows[0]
    workflows = [
        row
        for row in components["workflow_slot"]
        if row.id == audio.tts_workflow_slot_version_id
        and row.provider_config_version_id == provider.id
        and row.operation_kind == "tts"
    ]
    if len(workflows) != 1:
        raise CosyVoiceValidationError(
            "COSYVOICE_WORKFLOW_NOT_EXACT",
            "音频合同没有精确引用当前 CosyVoice TTS 工作流。",
        )
    workflow = workflows[0]
    issues = cosyvoice_workflow_contract_issues(workflow.node_info_list or [])
    if issues:
        raise CosyVoiceValidationError(
            "COSYVOICE_WORKFLOW_INVALID",
            f"CosyVoice 工作流合同存在 {len(issues)} 个问题。",
        )
    if (
        storage.backend_kind != "local"
        or storage.local_root_ref != CONNECTED_LOCAL_ASSET_ROOT_REF
        or "audio/wav" not in (storage.allowed_mime_types or [])
    ):
        raise CosyVoiceValidationError(
            "COSYVOICE_STORAGE_UNSUPPORTED",
            "CosyVoice 验收要求已连接且允许 audio/wav 的 V2 本地素材存储。",
        )
    return CosyVoiceValidationContract(configuration, provider, workflow, audio, storage)


def _manifest(contract: CosyVoiceValidationContract, text: str) -> dict[str, Any]:
    voices = {
        str(item.get("key")): item
        for item in (contract.audio.voice_presets or [])
        if isinstance(item, dict)
    }
    voice = voices.get(str(contract.audio.default_voice_key or ""))
    if not voice or not str(voice.get("provider_voice_id") or "").strip():
        raise CosyVoiceValidationError(
            "COSYVOICE_VALIDATION_VOICE_INVALID",
            "音频合同没有可用于真实验收的默认供应商音色。",
        )
    return {
        "schema_version": "cosyvoice-validation-request.v1",
        "input_contract": {
            "voiceover_text": text,
            "voice": {
                "key": voice["key"],
                "display_name": voice["display_name"],
                "provider_voice_id": voice["provider_voice_id"],
            },
            "speaking_rate": contract.audio.speaking_rate_default,
            "volume": contract.audio.volume_default,
        },
        "output_contract": {"media_type": "audio"},
        "provider": {
            "provider_key": contract.provider.provider_key,
            "adapter_kind": contract.provider.adapter_kind,
            "base_url": contract.provider.base_url,
            "api_key": contract.provider.api_key,
            "request_timeout_seconds": contract.provider.request_timeout_seconds,
        },
        "workflow": {
            "provider_workflow_id": contract.workflow.provider_workflow_id,
            "provider_workflow_version": contract.workflow.provider_workflow_version,
            "input_schema_version": contract.workflow.input_schema_version,
            "output_schema_version": contract.workflow.output_schema_version,
            "node_info_list": contract.workflow.node_info_list,
        },
        "storage_policy": {
            "backend_kind": contract.storage.backend_kind,
            "local_root_ref": contract.storage.local_root_ref,
            "allowed_mime_types": contract.storage.allowed_mime_types,
            "max_file_size_bytes": contract.storage.max_file_size_bytes,
        },
    }


def _contract_report(
    contract: CosyVoiceValidationContract,
    text: str,
) -> dict[str, Any]:
    manifest = _manifest(contract, text)
    voice = manifest["input_contract"]["voice"]
    api_key_configured = bool(str(contract.provider.api_key or "").strip())
    execution_enabled = _external_execution_enabled()
    status = (
        "credential_not_ready"
        if not api_key_configured
        else "execution_disabled"
        if not execution_enabled
        else "ready_for_paid_validation"
    )
    return {
        "schema_version": "cosyvoice-validation-report.v1",
        "status": status,
        "network_probe_performed": False,
        "configuration": {
            "id": contract.configuration.id,
            "version_number": contract.configuration.version_number,
            "config_hash": contract.configuration.config_hash,
        },
        "provider": {
            "id": contract.provider.id,
            "provider_key": contract.provider.provider_key,
            "adapter_kind": contract.provider.adapter_kind,
            "api_key_state": "configured" if api_key_configured else "missing",
            "execution_enabled": execution_enabled,
        },
        "workflow": {
            "id": contract.workflow.id,
            "slot_key": contract.workflow.slot_key,
            "model": contract.workflow.provider_workflow_id,
            "version": contract.workflow.provider_workflow_version,
        },
        "audio_contract": {
            "sample_rate": contract.audio.sample_rate,
            "channels": contract.audio.channels,
            "format": contract.audio.format,
            "voice_key": voice["key"],
            "provider_voice_id": voice["provider_voice_id"],
            "speaking_rate": contract.audio.speaking_rate_default,
            "volume": contract.audio.volume_default,
        },
        "validation_text": {
            "character_count": len(text),
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        },
    }


def _local_output_path(uri: str) -> Path:
    prefix = "runtime://"
    if not uri.startswith(prefix):
        raise CosyVoiceValidationError(
            "COSYVOICE_VALIDATION_OUTPUT_URI_INVALID",
            "CosyVoice 验收输出不是 V2 runtime URI。",
        )
    relative = PurePosixPath(uri[len(prefix):])
    if relative.is_absolute() or not relative.parts or ".." in relative.parts:
        raise CosyVoiceValidationError(
            "COSYVOICE_VALIDATION_OUTPUT_URI_INVALID",
            "CosyVoice 验收输出 URI 不安全。",
        )
    root = RUNTIME_ROOT.resolve()
    path = (root / PurePath(*relative.parts)).resolve()
    if not path.is_relative_to(root):
        raise CosyVoiceValidationError(
            "COSYVOICE_VALIDATION_OUTPUT_URI_INVALID",
            "CosyVoice 验收输出越过了 V2 runtime 目录。",
        )
    return path


def run_validation(
    contract: CosyVoiceValidationContract,
    *,
    text: str = DEFAULT_VALIDATION_TEXT,
    confirm_paid_call: bool = False,
    transport: CosyVoiceTransport | None = None,
) -> dict[str, Any]:
    normalized_text = " ".join(text.split())
    if not normalized_text or len(normalized_text) > 200:
        raise CosyVoiceValidationError(
            "COSYVOICE_VALIDATION_TEXT_INVALID",
            "验收文本必须为 1–200 个字符。",
        )
    report = _contract_report(contract, normalized_text)
    if not confirm_paid_call:
        return report
    if not str(contract.provider.api_key or "").strip():
        raise CosyVoiceValidationError(
            "COSYVOICE_CREDENTIAL_MISSING",
            "当前已发布配置没有 CosyVoice API Key，未执行网络调用。",
        )
    if not _external_execution_enabled():
        raise CosyVoiceValidationError(
            "EXTERNAL_PROVIDER_EXECUTION_DISABLED",
            "外部 Provider 执行未启用，未执行 CosyVoice 网络调用。",
        )
    fingerprint = hashlib.sha256(
        f"{contract.configuration.id}:{contract.workflow.id}:{uuid.uuid4().hex}:{normalized_text}".encode("utf-8")
    ).hexdigest()
    adapter = CosyVoiceAdapter(
        execution_enabled=True,
        **({"transport": transport} if transport is not None else {}),
    )
    try:
        response = adapter.execute(ProviderExecutionRequest(
            work_kind="generate_tts",
            request_fingerprint=fingerprint,
            request_manifest=_manifest(contract, normalized_text),
        ))
    except ProviderAdapterError as exc:
        raise CosyVoiceValidationError(exc.code, exc.detail) from exc
    output = response["outputs"][0]
    path = _local_output_path(str(output["uri"]))
    media = probe_media(path, "audio")
    if media["mime_type"] != "audio/wav":
        raise CosyVoiceValidationError(
            "COSYVOICE_VALIDATION_OUTPUT_INVALID",
            "真实验收输出没有通过 WAV 文件探测。",
        )
    return {
        **report,
        "status": "passed",
        "network_probe_performed": True,
        "request_id": response.get("request_id"),
        "usage": response.get("usage") if isinstance(response.get("usage"), dict) else {},
        "output": {
            "uri": output["uri"],
            "mime_type": output["mime_type"],
            "content_hash": output["content_hash"],
            "byte_size": path.stat().st_size,
            "duration_ms": media["duration_ms"],
            "sample_rate": output["sample_rate"],
            "channels": output["channels"],
        },
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="检查或显式执行一次当前已发布 CosyVoice 合同的真实付费验收。",
    )
    parser.add_argument("--configuration-id", help="指定已发布制作配置 ID；默认使用最新已发布配置。")
    parser.add_argument("--text", default=DEFAULT_VALIDATION_TEXT, help="1–200 字符的验收文本。")
    parser.add_argument(
        "--confirm-paid-call",
        action="store_true",
        help="明确授权一次真实 CosyVoice 网络调用；未提供时只检查合同与凭据状态。",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        with SessionLocal() as session:
            contract = load_validation_contract(session, args.configuration_id)
            report = run_validation(
                contract,
                text=args.text,
                confirm_paid_call=args.confirm_paid_call,
            )
    except CosyVoiceValidationError as exc:
        print(json.dumps({
            "schema_version": "cosyvoice-validation-report.v1",
            "status": "blocked",
            "network_probe_performed": False,
            "error_code": exc.code,
            "detail": exc.detail,
        }, ensure_ascii=False))
        return 2
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
