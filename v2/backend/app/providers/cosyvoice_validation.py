from __future__ import annotations

import hashlib

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..db.models import ConfigurationCommandReceipt, CosyVoiceValidationRun
from .contracts import CosyVoicePaidValidationCommand
from .cosyvoice import CosyVoiceTransport, HttpxCosyVoiceTransport
from v2.scripts.validate_cosyvoice_connection import (
    CosyVoiceValidationError,
    load_validation_contract,
    run_validation,
)


class CosyVoiceValidationConflictError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class _TrackingCosyVoiceTransport:
    def __init__(self, delegate: CosyVoiceTransport):
        self.delegate = delegate
        self.network_probe_performed = False

    def synthesize(self, url, api_key, payload, timeout, max_bytes):
        self.network_probe_performed = True
        return self.delegate.synthesize(url, api_key, payload, timeout, max_bytes)


def _run_read(row: CosyVoiceValidationRun) -> dict:
    return {
        "id": row.id,
        "production_config_version_id": row.production_config_version_id,
        "provider_config_version_id": row.provider_config_version_id,
        "workflow_slot_version_id": row.workflow_slot_version_id,
        "status": row.status,
        "network_probe_performed": row.network_probe_performed,
        "validation_text_sha256": row.validation_text_sha256,
        "validation_text_character_count": row.validation_text_character_count,
        "request_id": row.request_id,
        "usage": row.usage,
        "output": row.output,
        "error_code": row.error_code,
        "error_detail": row.error_detail,
        "created_by": row.created_by,
        "created_at": row.created_at.isoformat(),
    }


def cosyvoice_validation_workspace(
    session: Session,
    configuration_id: str | None = None,
) -> dict:
    try:
        contract = load_validation_contract(session, configuration_id)
        preflight = run_validation(contract)
    except CosyVoiceValidationError as exc:
        raise CosyVoiceValidationConflictError(exc.code, exc.detail) from exc
    runs = list(session.scalars(
        select(CosyVoiceValidationRun)
        .where(
            CosyVoiceValidationRun.production_config_version_id
            == contract.configuration.id
        )
        .order_by(CosyVoiceValidationRun.created_at.desc())
        .limit(20)
    ))
    return {
        "preflight": preflight,
        "validation_runs": [_run_read(row) for row in runs],
    }


def execute_cosyvoice_paid_validation(
    session: Session,
    payload: CosyVoicePaidValidationCommand,
    *,
    transport: CosyVoiceTransport | None = None,
) -> dict:
    receipt = session.scalar(select(ConfigurationCommandReceipt).where(
        ConfigurationCommandReceipt.command_id == payload.command_id,
    ))
    if receipt:
        if receipt.command_type != "cosyvoice.validation.execute":
            raise CosyVoiceValidationConflictError(
                "COMMAND_ID_REUSED",
                "该命令 ID 已用于其他系统配置操作。",
            )
        row = session.get(CosyVoiceValidationRun, receipt.result_id)
        if row is None:
            raise CosyVoiceValidationConflictError(
                "COMMAND_RESULT_MISSING",
                "CosyVoice 验收命令结果不存在。",
            )
        return _run_read(row)
    try:
        contract = load_validation_contract(session, payload.configuration_id)
        preflight = run_validation(
            contract,
            text=payload.validation_text,
        )
    except CosyVoiceValidationError as exc:
        raise CosyVoiceValidationConflictError(exc.code, exc.detail) from exc
    normalized_text = " ".join(payload.validation_text.split())
    text_sha256 = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
    if (
        contract.configuration.config_hash != payload.expected_config_hash
        or preflight["validation_text"]["sha256"]
        != payload.expected_validation_text_sha256
        or text_sha256 != payload.expected_validation_text_sha256
    ):
        raise CosyVoiceValidationConflictError(
            "COSYVOICE_VALIDATION_CONTRACT_CHANGED",
            "配置合同或验收文本已经变化，请重新执行只读预检。",
        )
    if preflight["status"] != "ready_for_paid_validation":
        raise CosyVoiceValidationConflictError(
            "COSYVOICE_PAID_VALIDATION_NOT_READY",
            "CosyVoice 凭据或外部执行授权尚未就绪，未执行网络调用。",
        )
    tracking_transport = _TrackingCosyVoiceTransport(
        transport or HttpxCosyVoiceTransport()
    )
    try:
        report = run_validation(
            contract,
            text=normalized_text,
            confirm_paid_call=True,
            transport=tracking_transport,
        )
        status = "passed"
        error_code = None
        error_detail = None
    except CosyVoiceValidationError as exc:
        report = preflight
        status = "blocked"
        error_code = exc.code
        error_detail = exc.detail
    row = CosyVoiceValidationRun(
        production_config_version_id=contract.configuration.id,
        provider_config_version_id=contract.provider.id,
        workflow_slot_version_id=contract.workflow.id,
        status=status,
        network_probe_performed=tracking_transport.network_probe_performed,
        validation_text_sha256=text_sha256,
        validation_text_character_count=len(normalized_text),
        request_id=report.get("request_id"),
        usage=report.get("usage") if isinstance(report.get("usage"), dict) else {},
        output=report.get("output") if isinstance(report.get("output"), dict) else {},
        error_code=error_code,
        error_detail=error_detail,
        created_by=payload.actor_id,
    )
    session.add(row)
    session.flush()
    session.add(ConfigurationCommandReceipt(
        command_id=payload.command_id,
        command_type="cosyvoice.validation.execute",
        result_type="cosyvoice_validation_run",
        result_id=row.id,
    ))
    session.commit()
    return _run_read(row)
