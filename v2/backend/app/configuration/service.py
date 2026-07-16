from __future__ import annotations

import hashlib
import json
from collections import Counter

from sqlalchemy.orm import Session

from ..db.models import (
    AudioConfigVersion,
    ConfigurationCommandReceipt,
    ConfigurationEvent,
    ConfigurationReference,
    ModelConfigVersion,
    PricingCatalogVersion,
    PricingRule,
    ProductionConfigComponent,
    ProductionConfigVersion,
    ProviderConfigVersion,
    StoragePolicyVersion,
    VideoSpecVersion,
    WorkflowSlotVersion,
    utc_now,
)
from ..repositories import ConfigurationRepository, SqlAlchemyConfigurationRepository
from .contracts import (
    CloneConfiguration,
    ConfigurationDraftBody,
    CreateConfiguration,
    PublishConfiguration,
    RetireConfiguration,
    ReviseConfiguration,
    ValidateConfiguration,
)


class ConfigurationConflictError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ConfigurationNotFoundError(ValueError):
    pass


COMPONENT_MODELS = {
    "provider": ProviderConfigVersion,
    "model": ModelConfigVersion,
    "workflow_slot": WorkflowSlotVersion,
    "video_spec": VideoSpecVersion,
    "audio": AudioConfigVersion,
    "storage": StoragePolicyVersion,
    "pricing_catalog": PricingCatalogVersion,
}


def _receipt_result(
    repository: ConfigurationRepository,
    receipt: ConfigurationCommandReceipt,
) -> ProductionConfigVersion:
    result = repository.configuration(receipt.result_id)
    if not result:
        raise ConfigurationConflictError("COMMAND_RESULT_MISSING", "命令结果不存在，不能静默重新执行。")
    return result


def _replay_receipt(
    repository: ConfigurationRepository,
    command_id: str,
    command_type: str,
) -> ProductionConfigVersion | None:
    receipt = repository.receipt(command_id)
    if not receipt:
        return None
    if receipt.command_type != command_type:
        raise ConfigurationConflictError(
            "COMMAND_ID_REUSED",
            f"命令 ID 已用于 {receipt.command_type}，不能用于 {command_type}。",
        )
    return _receipt_result(repository, receipt)


def _save_receipt(
    repository: ConfigurationRepository,
    command_id: str,
    command_type: str,
    result_id: str,
) -> None:
    repository.add(ConfigurationCommandReceipt(
        command_id=command_id,
        command_type=command_type,
        result_type="production_config_version",
        result_id=result_id,
    ))


def _event(
    repository: ConfigurationRepository,
    config_id: str,
    event_type: str,
    actor_id: str,
    command_id: str,
    data: dict | None = None,
) -> None:
    repository.add(ConfigurationEvent(
        production_config_version_id=config_id,
        event_type=event_type,
        actor_id=actor_id,
        command_id=command_id,
        data=data or {},
    ))


def _assert_unique_keys(draft: ConfigurationDraftBody) -> None:
    groups = {
        "providers": [item.provider_key for item in draft.providers],
        "models": [item.config_key for item in draft.models],
        "workflow_slots": [item.slot_key for item in draft.workflow_slots],
        "video_specs": [item.spec_key for item in draft.video_specs],
    }
    duplicates = {
        group: sorted(key for key, count in Counter(keys).items() if count > 1)
        for group, keys in groups.items()
    }
    duplicates = {group: keys for group, keys in duplicates.items() if keys}
    if duplicates:
        raise ConfigurationConflictError("DUPLICATE_COMPONENT_KEY", f"组件键重复：{duplicates}")


def _component_link(
    repository: ConfigurationRepository,
    config_id: str,
    component_type: str,
    component_id: str,
) -> None:
    repository.add(ProductionConfigComponent(
        production_config_version_id=config_id,
        component_type=component_type,
        component_version_id=component_id,
    ))


def _create_components(
    repository: ConfigurationRepository,
    config: ProductionConfigVersion,
    draft: ConfigurationDraftBody,
) -> None:
    _assert_unique_keys(draft)
    provider_by_key: dict[str, ProviderConfigVersion] = {}
    model_by_key: dict[str, ModelConfigVersion] = {}
    video_by_key: dict[str, VideoSpecVersion] = {}
    workflow_by_key: dict[str, WorkflowSlotVersion] = {}

    for item in draft.providers:
        row = ProviderConfigVersion(
            production_config_version_id=config.id,
            provider_key=item.provider_key,
            version_number=repository.next_component_version("provider", item.provider_key),
            display_name=item.display_name,
            adapter_kind=item.adapter_kind,
            region=item.region,
            base_url=str(item.base_url),
            credential_ref=item.credential_ref,
            capabilities=item.capabilities,
            request_timeout_seconds=item.request_timeout_seconds,
            poll_interval_seconds=item.poll_interval_seconds,
            max_concurrency=item.max_concurrency,
        )
        repository.add(row)
        repository.flush()
        provider_by_key[item.provider_key] = row
        _component_link(repository, config.id, "provider", row.id)

    for item in draft.video_specs:
        row = VideoSpecVersion(
            production_config_version_id=config.id,
            spec_key=item.spec_key,
            version_number=repository.next_component_version("video_spec", item.spec_key),
            display_name=item.display_name,
            width=item.width,
            height=item.height,
            aspect_ratio=item.aspect_ratio,
            fps=item.fps,
            duration_min_seconds=item.duration_min_seconds,
            duration_max_seconds=item.duration_max_seconds,
            frame_count_rule=item.frame_count_rule,
            container=item.container,
            video_codec=item.video_codec,
            pixel_format=item.pixel_format,
            bitrate_policy=item.bitrate_policy,
            safe_crop=item.safe_crop,
        )
        repository.add(row)
        repository.flush()
        video_by_key[item.spec_key] = row
        _component_link(repository, config.id, "video_spec", row.id)

    for item in draft.models:
        provider = provider_by_key.get(item.provider_key)
        if not provider:
            raise ConfigurationConflictError(
                "MODEL_PROVIDER_NOT_IN_DRAFT",
                f"模型 {item.config_key} 引用的供应商 {item.provider_key} 不在本配置草稿中。",
            )
        row = ModelConfigVersion(
            production_config_version_id=config.id,
            config_key=item.config_key,
            version_number=repository.next_component_version("model", item.config_key),
            display_name=item.display_name,
            agent_role=item.agent_role,
            provider_config_version_id=provider.id,
            provider_model_id=item.provider_model_id,
            input_contract_version=item.input_contract_version,
            output_schema_version=item.output_schema_version,
            prompt_contract_version=item.prompt_contract_version,
            context_window=item.context_window,
            max_output_tokens=item.max_output_tokens,
            sampling=item.sampling,
            capability_tags=item.capability_tags,
        )
        repository.add(row)
        repository.flush()
        model_by_key[item.config_key] = row
        _component_link(repository, config.id, "model", row.id)

    for item in draft.workflow_slots:
        provider = provider_by_key.get(item.provider_key)
        if not provider:
            raise ConfigurationConflictError(
                "WORKFLOW_PROVIDER_NOT_IN_DRAFT",
                f"工作流槽位 {item.slot_key} 引用的供应商 {item.provider_key} 不在本配置草稿中。",
            )
        model = model_by_key.get(item.model_config_key) if item.model_config_key else None
        if item.model_config_key and not model:
            raise ConfigurationConflictError(
                "WORKFLOW_MODEL_NOT_IN_DRAFT",
                f"工作流槽位 {item.slot_key} 引用的模型 {item.model_config_key} 不在本配置草稿中。",
            )
        missing_specs = [key for key in item.supported_video_spec_keys if key not in video_by_key]
        if missing_specs:
            raise ConfigurationConflictError(
                "WORKFLOW_VIDEO_SPEC_NOT_IN_DRAFT",
                f"工作流槽位 {item.slot_key} 引用了不存在的视频规格：{missing_specs}",
            )
        row = WorkflowSlotVersion(
            production_config_version_id=config.id,
            slot_key=item.slot_key,
            version_number=repository.next_component_version("workflow_slot", item.slot_key),
            display_name=item.display_name,
            operation_kind=item.operation_kind,
            provider_config_version_id=provider.id,
            provider_workflow_id=item.provider_workflow_id,
            provider_workflow_version=item.provider_workflow_version,
            model_config_version_id=model.id if model else None,
            input_schema_version=item.input_schema_version,
            output_schema_version=item.output_schema_version,
            node_info_list=[entry.model_dump() for entry in item.node_info_list],
            supported_video_spec_ids=[video_by_key[key].id for key in item.supported_video_spec_keys],
            capability_tags=item.capability_tags,
        )
        repository.add(row)
        repository.flush()
        workflow_by_key[item.slot_key] = row
        _component_link(repository, config.id, "workflow_slot", row.id)

    tts_slot = workflow_by_key.get(draft.audio.tts_workflow_slot_key) if draft.audio.tts_workflow_slot_key else None
    if draft.audio.tts_workflow_slot_key and not tts_slot:
        raise ConfigurationConflictError(
            "AUDIO_TTS_SLOT_NOT_IN_DRAFT",
            f"音频配置引用的 TTS 槽位 {draft.audio.tts_workflow_slot_key} 不在本配置草稿中。",
        )
    audio = AudioConfigVersion(
        production_config_version_id=config.id,
        config_key=draft.audio.config_key,
        version_number=repository.next_component_version("audio", draft.audio.config_key),
        display_name=draft.audio.display_name,
        supported_modes=draft.audio.supported_modes,
        tts_workflow_slot_version_id=tts_slot.id if tts_slot else None,
        default_voice_entity_version_id=draft.audio.default_voice_entity_version_id,
        sample_rate=draft.audio.sample_rate,
        channels=draft.audio.channels,
        format=draft.audio.format,
        speaking_rate_range={"min": draft.audio.speaking_rate_min, "max": draft.audio.speaking_rate_max},
        loudness_target=draft.audio.loudness_target,
        temporary_upload_policy_version_id=draft.audio.temporary_upload_policy_version_id,
    )
    repository.add(audio)
    repository.flush()
    _component_link(repository, config.id, "audio", audio.id)

    storage = StoragePolicyVersion(
        production_config_version_id=config.id,
        policy_key=draft.storage.policy_key,
        version_number=repository.next_component_version("storage", draft.storage.policy_key),
        display_name=draft.storage.display_name,
        backend_kind=draft.storage.backend_kind,
        region_ref=draft.storage.region_ref,
        bucket_ref=draft.storage.bucket_ref,
        credential_ref=draft.storage.credential_ref,
        allowed_mime_types=draft.storage.allowed_mime_types,
        max_file_size_bytes=draft.storage.max_file_size_bytes,
        public_url_policy=draft.storage.public_url_policy,
        lifecycle_days=draft.storage.lifecycle_days,
        local_root_ref=draft.storage.local_root_ref,
    )
    repository.add(storage)
    repository.flush()
    _component_link(repository, config.id, "storage", storage.id)

    if draft.pricing:
        pricing = PricingCatalogVersion(
            production_config_version_id=config.id,
            catalog_key=draft.pricing.catalog_key,
            version_number=repository.next_component_version("pricing_catalog", draft.pricing.catalog_key),
            display_name=draft.pricing.display_name,
            currency=draft.pricing.currency,
            confirmation_threshold=float(draft.pricing.confirmation_threshold),
            effective_from=draft.pricing.effective_from,
            effective_to=draft.pricing.effective_to,
        )
        repository.add(pricing)
        repository.flush()
        _component_link(repository, config.id, "pricing_catalog", pricing.id)
        seen_pricing_slots: set[str] = set()
        for rule in draft.pricing.rules:
            workflow = workflow_by_key.get(rule.workflow_slot_key)
            if not workflow:
                raise ConfigurationConflictError(
                    "PRICING_WORKFLOW_NOT_IN_DRAFT",
                    f"价格规则引用的工作流槽位 {rule.workflow_slot_key} 不在本配置草稿中。",
                )
            if rule.workflow_slot_key in seen_pricing_slots:
                raise ConfigurationConflictError(
                    "DUPLICATE_PRICING_WORKFLOW",
                    f"工作流槽位 {rule.workflow_slot_key} 存在重复价格规则。",
                )
            seen_pricing_slots.add(rule.workflow_slot_key)
            repository.add(PricingRule(
                pricing_catalog_version_id=pricing.id,
                provider_config_version_id=workflow.provider_config_version_id,
                workflow_slot_version_id=workflow.id,
                operation_kind=workflow.operation_kind,
                unit=rule.unit,
                unit_price=float(rule.unit_price),
                minimum_charge=float(rule.minimum_charge) if rule.minimum_charge is not None else None,
                estimated_runtime_seconds=float(rule.estimated_runtime_seconds) if rule.estimated_runtime_seconds is not None else None,
            ))
        repository.flush()


def _provider_details(row: ProviderConfigVersion) -> dict:
    return {
        "adapter_kind": row.adapter_kind,
        "region": row.region,
        "base_url": row.base_url,
        "credential_ref": row.credential_ref,
        "capabilities": row.capabilities,
        "request_timeout_seconds": row.request_timeout_seconds,
        "poll_interval_seconds": row.poll_interval_seconds,
        "max_concurrency": row.max_concurrency,
    }


def _component_summary(component_type: str, row, lookups: dict[str, dict[str, object]]) -> dict:
    if component_type == "provider":
        key, details = row.provider_key, _provider_details(row)
    elif component_type == "model":
        key = row.config_key
        details = {
            "agent_role": row.agent_role,
            "provider_config_version_id": row.provider_config_version_id,
            "provider_model_id": row.provider_model_id,
            "input_contract_version": row.input_contract_version,
            "output_schema_version": row.output_schema_version,
            "prompt_contract_version": row.prompt_contract_version,
            "context_window": row.context_window,
            "max_output_tokens": row.max_output_tokens,
            "sampling": row.sampling,
            "capability_tags": row.capability_tags,
        }
    elif component_type == "workflow_slot":
        key = row.slot_key
        details = {
            "operation_kind": row.operation_kind,
            "provider_config_version_id": row.provider_config_version_id,
            "provider_workflow_id": row.provider_workflow_id,
            "provider_workflow_version": row.provider_workflow_version,
            "model_config_version_id": row.model_config_version_id,
            "input_schema_version": row.input_schema_version,
            "output_schema_version": row.output_schema_version,
            "node_info_list": row.node_info_list,
            "supported_video_spec_ids": row.supported_video_spec_ids,
            "capability_tags": row.capability_tags,
            "validation_status": row.validation_status,
            "validation_report": row.validation_report,
        }
    elif component_type == "video_spec":
        key = row.spec_key
        details = {
            "width": row.width,
            "height": row.height,
            "aspect_ratio": row.aspect_ratio,
            "fps": row.fps,
            "duration_min_seconds": row.duration_min_seconds,
            "duration_max_seconds": row.duration_max_seconds,
            "frame_count_rule": row.frame_count_rule,
            "container": row.container,
            "video_codec": row.video_codec,
            "pixel_format": row.pixel_format,
            "bitrate_policy": row.bitrate_policy,
            "safe_crop": row.safe_crop,
        }
    elif component_type == "audio":
        key = row.config_key
        details = {
            "supported_modes": row.supported_modes,
            "tts_workflow_slot_version_id": row.tts_workflow_slot_version_id,
            "default_voice_entity_version_id": row.default_voice_entity_version_id,
            "sample_rate": row.sample_rate,
            "channels": row.channels,
            "format": row.format,
            "speaking_rate_range": row.speaking_rate_range,
            "loudness_target": row.loudness_target,
            "temporary_upload_policy_version_id": row.temporary_upload_policy_version_id,
        }
    elif component_type == "storage":
        key = row.policy_key
        details = {
            "backend_kind": row.backend_kind,
            "region_ref": row.region_ref,
            "bucket_ref": row.bucket_ref,
            "credential_ref": row.credential_ref,
            "allowed_mime_types": row.allowed_mime_types,
            "max_file_size_bytes": row.max_file_size_bytes,
            "public_url_policy": row.public_url_policy,
            "lifecycle_days": row.lifecycle_days,
            "local_root_ref": row.local_root_ref,
        }
    else:
        key = row.catalog_key
        details = {
            "currency": row.currency,
            "confirmation_threshold": row.confirmation_threshold,
            "effective_from": row.effective_from,
            "effective_to": row.effective_to,
            "rules": [{
                "id": rule.id,
                "provider_config_version_id": rule.provider_config_version_id,
                "workflow_slot_version_id": rule.workflow_slot_version_id,
                "operation_kind": rule.operation_kind,
                "unit": rule.unit,
                "unit_price": rule.unit_price,
                "minimum_charge": rule.minimum_charge,
                "estimated_runtime_seconds": rule.estimated_runtime_seconds,
            } for rule in lookups.get("pricing_rules", {}).get(row.id, [])],
        }
    return {
        "id": row.id,
        "component_type": component_type,
        "key": key,
        "version_number": row.version_number,
        "display_name": row.display_name,
        "status": row.status,
        "details": details,
    }


def _component_summaries(repository: ConfigurationRepository, config_id: str) -> list[dict]:
    rows = repository.component_rows(config_id)
    pricing_ids = [row.id for row in rows["pricing_catalog"]]
    pricing_rules = repository.pricing_rules(pricing_ids, ordered=True)
    lookups: dict[str, dict[str, object]] = {
        "pricing_rules": {
            catalog_id: [rule for rule in pricing_rules if rule.pricing_catalog_version_id == catalog_id]
            for catalog_id in pricing_ids
        }
    }
    return [
        _component_summary(component_type, row, lookups)
        for component_type in COMPONENT_MODELS
        for row in rows[component_type]
    ]


def _canonical_components(repository: ConfigurationRepository, config_id: str) -> list[dict]:
    return sorted(
        _component_summaries(repository, config_id),
        key=lambda item: (item["component_type"], item["key"], item["version_number"]),
    )


def _calculate_hash(repository: ConfigurationRepository, config: ProductionConfigVersion) -> str:
    components = []
    for item in _canonical_components(repository, config.id):
        stable = dict(item)
        stable.pop("status", None)
        components.append(stable)
    payload = {
        "config_key": config.config_key,
        "version_number": config.version_number,
        "components": components,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _validate(repository: ConfigurationRepository, config: ProductionConfigVersion) -> list[dict]:
    rows = repository.component_rows(config.id)
    errors: list[dict] = []
    providers = {row.id: row for row in rows["provider"]}
    models = {row.id: row for row in rows["model"]}
    workflows = {row.id: row for row in rows["workflow_slot"]}
    videos = {row.id: row for row in rows["video_spec"]}

    required_groups = ("provider", "workflow_slot", "video_spec", "audio", "storage")
    for group in required_groups:
        if not rows[group]:
            errors.append({"code": "COMPONENT_REQUIRED", "path": group, "message": f"缺少 {group} 组件。"})
    if len(rows["audio"]) != 1:
        errors.append({"code": "AUDIO_CONFIG_COUNT_INVALID", "path": "audio", "message": "必须且只能有一个音频配置。"})
    if len(rows["storage"]) != 1:
        errors.append({"code": "STORAGE_POLICY_COUNT_INVALID", "path": "storage", "message": "必须且只能有一个存储策略。"})

    for model in rows["model"]:
        if model.provider_config_version_id not in providers:
            errors.append({"code": "MODEL_PROVIDER_MISSING", "path": f"models.{model.config_key}.provider"})

    for workflow in rows["workflow_slot"]:
        slot_errors: list[dict] = []
        provider = providers.get(workflow.provider_config_version_id)
        if not provider:
            slot_errors.append({"code": "WORKFLOW_PROVIDER_MISSING", "path": "provider"})
        elif workflow.operation_kind not in provider.capabilities:
            slot_errors.append({
                "code": "PROVIDER_CAPABILITY_MISSING",
                "path": "operation_kind",
                "message": f"供应商未声明能力 {workflow.operation_kind}。",
            })
        if workflow.model_config_version_id and workflow.model_config_version_id not in models:
            slot_errors.append({"code": "WORKFLOW_MODEL_MISSING", "path": "model"})
        missing_specs = [item for item in workflow.supported_video_spec_ids if item not in videos]
        if missing_specs:
            slot_errors.append({"code": "WORKFLOW_VIDEO_SPEC_MISSING", "path": "supported_video_spec_ids", "ids": missing_specs})
        bindings = workflow.node_info_list or []
        seen: set[tuple[str, str]] = set()
        for index, binding in enumerate(bindings):
            required = ("node_id", "field_path", "value_source", "value_type", "required")
            missing = [key for key in required if key not in binding or binding[key] in (None, "")]
            if missing:
                slot_errors.append({
                    "code": "NODE_BINDING_INCOMPLETE",
                    "path": f"node_info_list.{index}",
                    "missing": missing,
                })
            identity = (str(binding.get("node_id", "")), str(binding.get("field_path", "")))
            if identity in seen:
                slot_errors.append({"code": "NODE_BINDING_DUPLICATE", "path": f"node_info_list.{index}"})
            seen.add(identity)
        workflow.validation_report = slot_errors
        workflow.validation_status = "invalid" if slot_errors else "valid"
        errors.extend({**error, "slot_key": workflow.slot_key} for error in slot_errors)

    if rows["audio"]:
        audio = rows["audio"][0]
        if "voiceover" in audio.supported_modes:
            slot = workflows.get(audio.tts_workflow_slot_version_id)
            if not slot or slot.operation_kind != "tts":
                errors.append({
                    "code": "VOICEOVER_TTS_SLOT_REQUIRED",
                    "path": "audio.tts_workflow_slot_version_id",
                    "message": "支持旁白时必须精确绑定 operation_kind=tts 的工作流槽位。",
                })
        elif audio.tts_workflow_slot_version_id:
            errors.append({
                "code": "OFF_AUDIO_HAS_TTS_SLOT",
                "path": "audio.tts_workflow_slot_version_id",
                "message": "仅关闭音频的配置不得绑定 TTS 槽位。",
            })
    if rows["storage"]:
        storage = rows["storage"][0]
        if storage.backend_kind == "local" and not storage.local_root_ref:
            errors.append({"code": "LOCAL_ROOT_REQUIRED", "path": "storage.local_root_ref"})
        if storage.backend_kind == "oss":
            missing = [field for field in ("region_ref", "bucket_ref", "credential_ref", "lifecycle_days") if not getattr(storage, field)]
            if missing:
                errors.append({"code": "OSS_FIELDS_REQUIRED", "path": "storage", "missing": missing})
    if len(rows["pricing_catalog"]) > 1:
        errors.append({"code": "PRICING_CATALOG_COUNT_INVALID", "path": "pricing", "message": "一个配置版本最多只能有一个价格目录。"})
    if rows["pricing_catalog"]:
        pricing = rows["pricing_catalog"][0]
        rules = repository.pricing_rules([pricing.id])
        if not rules:
            errors.append({"code": "PRICING_RULE_REQUIRED", "path": "pricing.rules", "message": "价格目录至少需要一条精确工作流规则。"})
        for rule in rules:
            workflow = workflows.get(rule.workflow_slot_version_id)
            if not workflow:
                errors.append({"code": "PRICING_WORKFLOW_MISSING", "path": f"pricing.rules.{rule.id}"})
            elif workflow.provider_config_version_id != rule.provider_config_version_id or workflow.operation_kind != rule.operation_kind:
                errors.append({"code": "PRICING_RULE_MISMATCH", "path": f"pricing.rules.{rule.id}", "message": "价格规则与工作流供应商或操作类型不一致。"})
            if rule.unit == "runtime_second" and (rule.estimated_runtime_seconds is None or rule.estimated_runtime_seconds <= 0):
                errors.append({"code": "PRICING_RUNTIME_ESTIMATE_REQUIRED", "path": f"pricing.rules.{rule.id}.estimated_runtime_seconds"})
            elif rule.unit != "runtime_second" and rule.estimated_runtime_seconds is not None:
                errors.append({"code": "PRICING_RUNTIME_ESTIMATE_NOT_APPLICABLE", "path": f"pricing.rules.{rule.id}.estimated_runtime_seconds"})
    return errors


def create_configuration(session: Session, payload: CreateConfiguration) -> dict:
    repository = SqlAlchemyConfigurationRepository(session)
    replay = _replay_receipt(repository, payload.command_id, "configuration.create")
    if replay:
        return _configuration_read(repository, replay)
    draft = payload.configuration
    _assert_unique_keys(draft)
    version_number = repository.next_configuration_version(draft.config_key)
    config = ProductionConfigVersion(
        config_key=draft.config_key,
        version_number=version_number,
        display_name=draft.display_name,
        description=draft.description,
        created_by=payload.actor_id,
    )
    repository.add(config)
    repository.flush()
    _create_components(repository, config, draft)
    _save_receipt(repository, payload.command_id, "configuration.create", config.id)
    _event(repository, config.id, "configuration.draft_created.v1", payload.actor_id, payload.command_id, {
        "version_number": config.version_number,
        "component_count": len(_component_summaries(repository, config.id)),
    })
    session.commit()
    return _configuration_read(repository, config)


def revise_configuration(
    session: Session,
    config: ProductionConfigVersion,
    payload: ReviseConfiguration,
) -> dict:
    repository = SqlAlchemyConfigurationRepository(session)
    replay = _replay_receipt(repository, payload.command_id, "configuration.revise")
    if replay:
        return _configuration_read(repository, replay)
    if config.status not in {"draft", "validation_failed", "ready"}:
        raise ConfigurationConflictError("CONFIGURATION_IMMUTABLE", "已发布或停用配置不能原地修改。")
    if config.row_version != payload.expected_row_version:
        raise ConfigurationConflictError("CONFIGURATION_VERSION_CONFLICT", "配置已被其他命令修改，请刷新。")
    if payload.configuration.config_key != config.config_key:
        raise ConfigurationConflictError("CONFIG_KEY_IMMUTABLE", "配置系列键不能在草稿修订中改变。")
    _assert_unique_keys(payload.configuration)
    repository.delete_components(config.id)
    config.display_name = payload.configuration.display_name
    config.description = payload.configuration.description
    config.status = "draft"
    config.validation_report = []
    config.config_hash = None
    config.row_version += 1
    _create_components(repository, config, payload.configuration)
    _save_receipt(repository, payload.command_id, "configuration.revise", config.id)
    _event(repository, config.id, "configuration.draft_revised.v1", payload.actor_id, payload.command_id, {
        "row_version": config.row_version,
    })
    session.commit()
    return _configuration_read(repository, config)


def validate_configuration(
    session: Session,
    config: ProductionConfigVersion,
    payload: ValidateConfiguration,
) -> dict:
    repository = SqlAlchemyConfigurationRepository(session)
    replay = _replay_receipt(repository, payload.command_id, "configuration.validate")
    if replay:
        return _configuration_read(repository, replay)
    if config.status not in {"draft", "validation_failed"}:
        raise ConfigurationConflictError("CONFIGURATION_NOT_VALIDATABLE", f"配置状态 {config.status} 不能校验。")
    if config.row_version != payload.expected_row_version:
        raise ConfigurationConflictError("CONFIGURATION_VERSION_CONFLICT", "配置已被其他命令修改，请刷新。")
    config.status = "validating"
    _event(repository, config.id, "configuration.validation_started.v1", payload.actor_id, payload.command_id)
    errors = _validate(repository, config)
    config.validation_report = errors
    config.config_hash = None if errors else _calculate_hash(repository, config)
    config.status = "validation_failed" if errors else "ready"
    config.row_version += 1
    _event(
        repository,
        config.id,
        "configuration.validation_failed.v1" if errors else "configuration.validated.v1",
        payload.actor_id,
        payload.command_id,
        {"errors": errors, "config_hash": config.config_hash},
    )
    _save_receipt(repository, payload.command_id, "configuration.validate", config.id)
    session.commit()
    return _configuration_read(repository, config)


def publish_configuration(
    session: Session,
    config: ProductionConfigVersion,
    payload: PublishConfiguration,
) -> dict:
    repository = SqlAlchemyConfigurationRepository(session)
    replay = _replay_receipt(repository, payload.command_id, "configuration.publish")
    if replay:
        return _configuration_read(repository, replay)
    if config.status != "ready":
        raise ConfigurationConflictError("CONFIGURATION_NOT_READY", "配置必须先通过确定性校验。")
    if config.row_version != payload.expected_row_version:
        raise ConfigurationConflictError("CONFIGURATION_VERSION_CONFLICT", "配置已变化，请刷新后重新确认。")
    if not payload.confirm_high_risk_changes:
        raise ConfigurationConflictError("HIGH_RISK_CONFIRMATION_REQUIRED", "发布包含供应商、工作流和媒体规格，必须强确认。")
    recalculated = _calculate_hash(repository, config)
    if not config.config_hash or config.config_hash != recalculated:
        raise ConfigurationConflictError("CONFIGURATION_HASH_MISMATCH", "配置内容在校验后发生变化，必须重新校验。")
    now = utc_now()
    config.status = "published"
    config.published_at = now
    config.row_version += 1
    for rows in repository.component_rows(config.id).values():
        for row in rows:
            row.status = "published"
            row.published_at = now
    _event(repository, config.id, "configuration.published.v1", payload.actor_id, payload.command_id, {
        "config_hash": config.config_hash,
        "component_ids": [item["id"] for item in _component_summaries(repository, config.id)],
    })
    _save_receipt(repository, payload.command_id, "configuration.publish", config.id)
    session.commit()
    return _configuration_read(repository, config)


def retire_configuration(
    session: Session,
    config: ProductionConfigVersion,
    payload: RetireConfiguration,
) -> dict:
    repository = SqlAlchemyConfigurationRepository(session)
    replay = _replay_receipt(repository, payload.command_id, "configuration.retire")
    if replay:
        return _configuration_read(repository, replay)
    if config.status != "published":
        raise ConfigurationConflictError("CONFIGURATION_NOT_PUBLISHED", "只有已发布配置可以停用。")
    if config.row_version != payload.expected_row_version:
        raise ConfigurationConflictError("CONFIGURATION_VERSION_CONFLICT", "配置已变化，请刷新。")
    refs = repository.references(config.id)
    if refs and not payload.confirm_reference_impact:
        raise ConfigurationConflictError("REFERENCE_IMPACT_CONFIRMATION_REQUIRED", "配置已有引用，停用前必须确认引用范围。")
    config.status = "retired"
    config.row_version += 1
    for rows in repository.component_rows(config.id).values():
        for row in rows:
            row.status = "retired"
    _event(repository, config.id, "configuration.retired.v1", payload.actor_id, payload.command_id, {
        "references": [{"ref_type": item.ref_type, "ref_id": item.ref_id} for item in refs],
    })
    _save_receipt(repository, payload.command_id, "configuration.retire", config.id)
    session.commit()
    return _configuration_read(repository, config)


def _draft_from_config(
    repository: ConfigurationRepository,
    config: ProductionConfigVersion,
    display_name: str | None = None,
) -> ConfigurationDraftBody:
    components = _component_summaries(repository, config.id)
    by_type: dict[str, list[dict]] = {key: [] for key in COMPONENT_MODELS}
    for item in components:
        by_type[item["component_type"]].append(item)
    provider_key_by_id = {item["id"]: item["key"] for item in by_type["provider"]}
    model_key_by_id = {item["id"]: item["key"] for item in by_type["model"]}
    video_key_by_id = {item["id"]: item["key"] for item in by_type["video_spec"]}
    workflow_key_by_id = {item["id"]: item["key"] for item in by_type["workflow_slot"]}
    providers = [{"provider_key": item["key"], "display_name": item["display_name"], **item["details"]} for item in by_type["provider"]]
    models = [{
        "config_key": item["key"],
        "display_name": item["display_name"],
        **{key: value for key, value in item["details"].items() if key != "provider_config_version_id"},
        "provider_key": provider_key_by_id[item["details"]["provider_config_version_id"]],
    } for item in by_type["model"]]
    workflows = [{
        "slot_key": item["key"],
        "display_name": item["display_name"],
        **{key: value for key, value in item["details"].items() if key not in {
            "provider_config_version_id", "model_config_version_id", "supported_video_spec_ids",
            "validation_status", "validation_report",
        }},
        "provider_key": provider_key_by_id[item["details"]["provider_config_version_id"]],
        "model_config_key": model_key_by_id.get(item["details"]["model_config_version_id"]),
        "supported_video_spec_keys": [video_key_by_id[value] for value in item["details"]["supported_video_spec_ids"]],
    } for item in by_type["workflow_slot"]]
    videos = [{"spec_key": item["key"], "display_name": item["display_name"], **item["details"]} for item in by_type["video_spec"]]
    audio_item = by_type["audio"][0]
    audio_details = audio_item["details"]
    audio = {
        "config_key": audio_item["key"],
        "display_name": audio_item["display_name"],
        **{key: value for key, value in audio_details.items() if key not in {
            "tts_workflow_slot_version_id", "speaking_rate_range",
        }},
        "tts_workflow_slot_key": workflow_key_by_id.get(audio_details["tts_workflow_slot_version_id"]),
        "speaking_rate_min": audio_details["speaking_rate_range"]["min"],
        "speaking_rate_max": audio_details["speaking_rate_range"]["max"],
    }
    storage_item = by_type["storage"][0]
    storage = {"policy_key": storage_item["key"], "display_name": storage_item["display_name"], **storage_item["details"]}
    pricing = None
    if by_type["pricing_catalog"]:
        pricing_item = by_type["pricing_catalog"][0]
        pricing_details = pricing_item["details"]
        pricing = {
            "catalog_key": pricing_item["key"],
            "display_name": pricing_item["display_name"],
            "currency": pricing_details["currency"],
            "confirmation_threshold": pricing_details["confirmation_threshold"],
            "effective_from": pricing_details["effective_from"],
            "effective_to": pricing_details["effective_to"],
            "rules": [{
                "workflow_slot_key": workflow_key_by_id[rule["workflow_slot_version_id"]],
                "unit": rule["unit"],
                "unit_price": rule["unit_price"],
                "minimum_charge": rule["minimum_charge"],
                "estimated_runtime_seconds": rule["estimated_runtime_seconds"],
            } for rule in pricing_details["rules"]],
        }
    return ConfigurationDraftBody.model_validate({
        "config_key": config.config_key,
        "display_name": display_name or f"{config.display_name} 副本",
        "description": config.description,
        "providers": providers,
        "models": models,
        "workflow_slots": workflows,
        "video_specs": videos,
        "audio": audio,
        "storage": storage,
        "pricing": pricing,
    })


def clone_configuration(
    session: Session,
    config: ProductionConfigVersion,
    payload: CloneConfiguration,
) -> dict:
    repository = SqlAlchemyConfigurationRepository(session)
    replay = _replay_receipt(repository, payload.command_id, "configuration.clone")
    if replay:
        return _configuration_read(repository, replay)
    if config.status not in {"published", "retired"}:
        raise ConfigurationConflictError("CONFIGURATION_NOT_CLONEABLE", "只有已发布或已停用配置可以复制为新草稿。")
    draft = _draft_from_config(repository, config, payload.display_name)
    new_config = ProductionConfigVersion(
        config_key=config.config_key,
        version_number=repository.next_configuration_version(config.config_key),
        display_name=draft.display_name,
        description=draft.description,
        supersedes_version_id=config.id,
        created_by=payload.actor_id,
    )
    repository.add(new_config)
    repository.flush()
    _create_components(repository, new_config, draft)
    _save_receipt(repository, payload.command_id, "configuration.clone", new_config.id)
    _event(repository, new_config.id, "configuration.draft_created.v1", payload.actor_id, payload.command_id, {
        "supersedes_version_id": config.id,
    })
    session.commit()
    return _configuration_read(repository, new_config)


def list_configurations(session: Session) -> list[dict]:
    repository = SqlAlchemyConfigurationRepository(session)
    configs = repository.configurations()
    result = []
    for config in configs:
        result.append({
            "id": config.id,
            "config_key": config.config_key,
            "version_number": config.version_number,
            "display_name": config.display_name,
            "description": config.description,
            "status": config.status,
            "row_version": config.row_version,
            "config_hash": config.config_hash,
            "component_count": len(_component_summaries(repository, config.id)),
            "validation_error_count": len(config.validation_report or []),
            "published_at": config.published_at,
            "updated_at": config.updated_at,
        })
    return result


def require_configuration(session: Session, config_id: str) -> ProductionConfigVersion:
    config = SqlAlchemyConfigurationRepository(session).configuration(config_id)
    if not config:
        raise ConfigurationNotFoundError("System configuration version not found")
    return config


def configuration_read(session: Session, config: ProductionConfigVersion) -> dict:
    return _configuration_read(SqlAlchemyConfigurationRepository(session), config)


def _configuration_read(
    repository: ConfigurationRepository,
    config: ProductionConfigVersion,
) -> dict:
    refs = repository.references(config.id)
    return {
        "id": config.id,
        "config_key": config.config_key,
        "version_number": config.version_number,
        "display_name": config.display_name,
        "description": config.description,
        "status": config.status,
        "supersedes_version_id": config.supersedes_version_id,
        "row_version": config.row_version,
        "config_hash": config.config_hash,
        "validation_report": config.validation_report or [],
        "created_by": config.created_by,
        "published_at": config.published_at,
        "created_at": config.created_at,
        "updated_at": config.updated_at,
        "components": _component_summaries(repository, config.id),
        "references": [{
            "ref_type": item.ref_type,
            "ref_id": item.ref_id,
            "created_at": item.created_at,
        } for item in refs],
    }


def configuration_diff(
    session: Session,
    config: ProductionConfigVersion,
    base: ProductionConfigVersion,
) -> dict:
    repository = SqlAlchemyConfigurationRepository(session)
    current = _semantic_components(repository, config.id)
    previous = _semantic_components(repository, base.id)
    changed = []
    high_risk = []
    for identity in sorted(set(current) | set(previous)):
        before, after = previous.get(identity), current.get(identity)
        if before == after:
            continue
        changed.append({"component_type": identity[0], "key": identity[1], "before": before, "after": after})
        if identity[0] in {"provider", "model", "workflow_slot", "video_spec", "audio", "storage"}:
            high_risk.append(f"{identity[0]}:{identity[1]}")
    return {
        "version_id": config.id,
        "base_version_id": base.id,
        "changed_components": changed,
        "high_risk_changes": high_risk,
        "incurs_production_cost": False,
    }


def _semantic_components(
    repository: ConfigurationRepository,
    config_id: str,
) -> dict[tuple[str, str], dict]:
    items = _canonical_components(repository, config_id)
    keys_by_type = {
        component_type: {
            item["id"]: item["key"]
            for item in items
            if item["component_type"] == component_type
        }
        for component_type in COMPONENT_MODELS
    }
    semantic: dict[tuple[str, str], dict] = {}
    for item in items:
        details = json.loads(json.dumps(item["details"], ensure_ascii=False, default=str))
        if item["component_type"] in {"model", "workflow_slot"}:
            provider_id = details.pop("provider_config_version_id", None)
            details["provider_key"] = keys_by_type["provider"].get(provider_id)
        if item["component_type"] == "workflow_slot":
            model_id = details.pop("model_config_version_id", None)
            details["model_config_key"] = keys_by_type["model"].get(model_id)
            video_ids = details.pop("supported_video_spec_ids", [])
            details["supported_video_spec_keys"] = [
                keys_by_type["video_spec"].get(video_id) for video_id in video_ids
            ]
            details.pop("validation_status", None)
            details.pop("validation_report", None)
        if item["component_type"] == "audio":
            workflow_id = details.pop("tts_workflow_slot_version_id", None)
            details["tts_workflow_slot_key"] = keys_by_type["workflow_slot"].get(workflow_id)
        if item["component_type"] == "pricing_catalog":
            details["rules"] = sorted([{
                "workflow_slot_key": keys_by_type["workflow_slot"].get(rule["workflow_slot_version_id"]),
                "operation_kind": rule["operation_kind"],
                "unit": rule["unit"],
                "unit_price": rule["unit_price"],
                "minimum_charge": rule["minimum_charge"],
                "estimated_runtime_seconds": rule["estimated_runtime_seconds"],
            } for rule in details["rules"]], key=lambda rule: (rule["workflow_slot_key"] or "", rule["operation_kind"]))
        identity = (item["component_type"], item["key"])
        semantic[identity] = {
            "component_type": item["component_type"],
            "key": item["key"],
            "display_name": item["display_name"],
            "details": details,
        }
    return semantic


def component_versions(session: Session, component_type: str) -> list[dict]:
    if component_type not in COMPONENT_MODELS:
        raise ConfigurationNotFoundError("Unknown configuration component type")
    repository = SqlAlchemyConfigurationRepository(session)
    rows = repository.all_components(component_type)
    lookups: dict[str, dict[str, object]] = {}
    if component_type == "pricing_catalog":
        catalog_ids = [row.id for row in rows]
        rules = repository.pricing_rules(catalog_ids, ordered=True)
        lookups["pricing_rules"] = {
            catalog_id: [rule for rule in rules if rule.pricing_catalog_version_id == catalog_id]
            for catalog_id in catalog_ids
        }
    return [_component_summary(component_type, row, lookups) for row in rows]


def workflow_slot_versions(session: Session, slot_key: str) -> list[dict]:
    rows = SqlAlchemyConfigurationRepository(session).workflow_slot_versions(slot_key)
    return [_component_summary("workflow_slot", row, {}) for row in rows]
