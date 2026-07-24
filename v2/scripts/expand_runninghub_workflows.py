from __future__ import annotations

import uuid

from v2.backend.app.configuration.contracts import (
    CloneConfiguration,
    PricingRuleDraft,
    PublishConfiguration,
    RetireConfiguration,
    ReviseConfiguration,
    ValidateConfiguration,
    WorkflowSlotDraft,
)
from v2.backend.app.configuration.service import (
    _draft_from_config,
    clone_configuration,
    publish_configuration,
    retire_configuration,
    revise_configuration,
    validate_configuration,
)
from v2.backend.app.db.models import ProductionConfigVersion
from v2.backend.app.db.session import SessionLocal
from v2.backend.app.repositories import SqlAlchemyConfigurationRepository


REQUIRED_SLOT_KEYS = {
    "runninghub-keyframe-text-v2",
    "runninghub-keyframe-identity-v2",
    "runninghub-keyframe-style-reference-v2",
    "runninghub-broll-text-video-v2",
    "runninghub-three-frame-video-v2",
}
TARGET_CONFIG_NAME = "片场 V2 当前制作配置 v49"
TARGET_SLOT_NAMES = {
    "2069607607387639810": "首帧生视频",
    "2072296894507872257": "首中尾帧生视频",
}


def binding(node_id: str, field_path: str, value_source: str, value_type: str, required: bool = True) -> dict:
    return {
        "node_id": node_id,
        "field_path": field_path,
        "value_source": value_source,
        "value_type": value_type,
        "required": required,
    }


def workflow_slots(provider_key: str, video_spec_key: str) -> list[WorkflowSlotDraft]:
    common = {
        "provider_key": provider_key,
        "provider_workflow_version": None,
        "model_config_key": None,
        "supported_video_spec_keys": [video_spec_key],
    }
    return [
        WorkflowSlotDraft.model_validate({
            **common,
            "slot_key": "runninghub-keyframe-text-v2",
            "display_name": "纯文本关键帧",
            "operation_kind": "image_generation",
            "provider_workflow_id": "2069402773254397953",
            "input_schema_version": "v2.keyframe-text-input.v1",
            "output_schema_version": "v2.image-output.v1",
            "capability_tags": ["text_to_image", "keyframe"],
            "node_info_list": [
                binding("63", "text", "shot.visual_prompt", "string"),
                binding("72", "text", "shot.negative_prompt", "string", False),
                binding("64", "width", "video_spec.width", "integer"),
                binding("64", "height", "video_spec.height", "integer"),
                binding("64", "batch_size", "literal:1", "integer"),
                binding("66", "seed", "seed", "integer"),
                binding("66", "steps", "literal:8", "integer"),
                binding("66", "cfg", "literal:1", "number"),
                binding("66", "denoise", "literal:1", "number"),
                binding("9", "filename_prefix", 'literal:"v2/keyframe-text"', "string"),
            ],
        }),
        WorkflowSlotDraft.model_validate({
            **common,
            "slot_key": "runninghub-keyframe-identity-v2",
            "display_name": "人物一致性关键帧",
            "operation_kind": "image_generation",
            "provider_workflow_id": "2073414172825706497",
            "input_schema_version": "v2.keyframe-primary-reference-input.v1",
            "output_schema_version": "v2.image-output.v1",
            "capability_tags": ["image_to_image", "identity_reference", "keyframe"],
            "node_info_list": [
                binding("34", "value", "shot.visual_prompt", "string"),
                binding("3", "prompt", "shot.negative_prompt", "string", False),
                binding("2", "image", "reference_image.primary", "image"),
                binding("8", "value", "video_spec.short_side", "integer"),
                binding("27", "seed", "seed", "integer"),
                binding("24", "steps", "literal:8", "integer"),
                binding("24", "cfg", "literal:1", "number"),
                binding("24", "denoise", "literal:1.0", "number"),
                binding("48", "filename_prefix", 'literal:"v2/keyframe-identity"', "string"),
            ],
        }),
        WorkflowSlotDraft.model_validate({
            **common,
            "slot_key": "runninghub-keyframe-style-reference-v2",
            "display_name": "风格参考关键帧",
            "operation_kind": "image_generation",
            "provider_workflow_id": "2066342754241835010",
            "input_schema_version": "v2.keyframe-primary-reference-input.v1",
            "output_schema_version": "v2.image-output.v1",
            "capability_tags": ["image_to_image", "style_reference", "keyframe"],
            "node_info_list": [
                binding("39", "text", "shot.visual_prompt", "string"),
                binding("40", "text", "shot.negative_prompt", "string", False),
                binding("81", "image", "reference_image.primary", "image"),
                binding("88", "width", "video_spec.width", "integer"),
                binding("88", "height", "video_spec.height", "integer"),
                binding("86", "seed", "seed", "integer"),
                binding("86", "steps", "literal:20", "integer"),
                binding("86", "cfg", "literal:8", "number"),
                binding("86", "denoise", "literal:0.7", "number"),
                binding("50", "filename_prefix", 'literal:"v2/keyframe-style-reference"', "string"),
            ],
        }),
        WorkflowSlotDraft.model_validate({
            **common,
            "slot_key": "runninghub-three-frame-video-v2",
            "display_name": "首中尾帧生视频",
            "operation_kind": "multi_frame_video_generation",
            "provider_workflow_id": "2072296894507872257",
            "input_schema_version": "v2.three-frame-video-input.v1",
            "output_schema_version": "v2.video-output.v1",
            "capability_tags": ["image_to_video", "multi_frame", "three_frame"],
            "node_info_list": [
                binding("447", "image", "source_image.start", "image"),
                binding("448", "image", "source_image.middle", "image"),
                binding("449", "image", "source_image.end", "image"),
                binding("422", "value", "shot.visual_prompt", "string"),
                binding("417", "text", "shot.negative_prompt", "string", False),
                binding("418", "value", "literal:false", "boolean"),
                binding("410", "value", "video_spec.long_side", "integer"),
                binding("436", "value", "duration_seconds", "number"),
                binding("412", "value", "video_spec.fps", "integer"),
                binding("424", "width", "video_spec.width", "integer"),
                binding("424", "height", "video_spec.height", "integer"),
                binding("424", "length", "video_spec.frame_count", "integer"),
                binding("373", "frames_number", "video_spec.frame_count", "integer"),
                binding("373", "frame_rate", "video_spec.fps", "integer"),
                binding("413", "frame_rate", "video_spec.fps", "integer"),
                binding("446", "frame_idx_1", "literal:0", "integer"),
                binding("446", "frame_idx_2", "video_spec.middle_frame_index", "integer"),
                binding("446", "frame_idx_3", "video_spec.last_frame_index", "integer"),
                binding("362", "noise_seed", "seed", "integer"),
                binding("363", "noise_seed", "seed", "integer"),
                binding("413", "filename_prefix", 'literal:"v2/three-frame-video"', "string"),
            ],
        }),
        WorkflowSlotDraft.model_validate({
            **common,
            "slot_key": "runninghub-broll-text-video-v2",
            "display_name": "纯文本 B-roll 视频",
            "operation_kind": "text_to_video_generation",
            "provider_workflow_id": "2071227330307125249",
            "input_schema_version": "v2.text-to-video-input.v1",
            "output_schema_version": "v2.video-output.v1",
            "capability_tags": ["text_to_video", "broll"],
            "node_info_list": [
                binding("73", "text", "shot.visual_prompt", "string"),
                binding("25", "text", "shot.negative_prompt", "string", False),
                binding("43", "value", "video_spec.width", "integer"),
                binding("44", "value", "video_spec.height", "integer"),
                binding("74", "value", "duration_seconds", "number"),
                binding("20", "value", "video_spec.fps", "integer"),
                binding("21", "value", "video_spec.fps", "integer"),
                binding("40", "frame_rate", "video_spec.fps", "integer"),
                binding("28", "noise_seed", "seed", "integer"),
                binding("46", "noise_seed", "seed", "integer"),
            ],
        }),
    ]


def main() -> None:
    with SessionLocal() as session:
        source = session.query(ProductionConfigVersion).filter(
            ProductionConfigVersion.status == "published",
        ).order_by(ProductionConfigVersion.published_at.desc(), ProductionConfigVersion.id.desc()).first()
        if not source:
            raise RuntimeError("No published source configuration was found.")
        repository = SqlAlchemyConfigurationRepository(session)
        current_draft = _draft_from_config(repository, source, source.display_name)
        current_slot_keys = {slot.slot_key for slot in current_draft.workflow_slots}
        first_frame = next(slot for slot in current_draft.workflow_slots if slot.provider_workflow_id == "2069607607387639810")
        negative_optional = any(
            row.value_source == "shot.negative_prompt" and row.required is False
            for row in first_frame.node_info_list
        )
        names_current = all(
            any(slot.provider_workflow_id == workflow_id and slot.display_name == display_name for slot in current_draft.workflow_slots)
            for workflow_id, display_name in TARGET_SLOT_NAMES.items()
        )
        if REQUIRED_SLOT_KEYS.issubset(current_slot_keys) and negative_optional and names_current and source.display_name == TARGET_CONFIG_NAME:
            print(f"Already published: {source.id} v{source.version_number}")
            return

        cloned = clone_configuration(session, source, CloneConfiguration(
            command_id=f"workflow-expand-clone-{uuid.uuid4()}",
            actor_id="local-user",
            display_name=TARGET_CONFIG_NAME,
        ))
        config = session.get(ProductionConfigVersion, cloned["id"])
        assert config is not None
        draft = _draft_from_config(repository, config, config.display_name)
        draft.display_name = TARGET_CONFIG_NAME
        draft.description = "DeepSeek V4 Flash 文本智能体与 RunningHub 多工作流生产配置；工作流由用户逐镜头显式选择。"
        runninghub = next(item for item in draft.providers if item.adapter_kind == "runninghub")
        video_spec = draft.video_specs[0]
        runninghub.capabilities = sorted(set(runninghub.capabilities + ["text_to_video_generation", "multi_frame_video_generation"]))
        removed_slot_keys = {
            slot.slot_key for slot in draft.workflow_slots
            if slot.provider_workflow_id == "2072296894507872257" and slot.operation_kind == "image_generation"
        }
        draft.workflow_slots = [slot for slot in draft.workflow_slots if slot.slot_key not in removed_slot_keys]
        if draft.pricing is not None:
            draft.pricing.rules = [rule for rule in draft.pricing.rules if rule.workflow_slot_key not in removed_slot_keys]
        existing_slot_keys = {slot.slot_key for slot in draft.workflow_slots}
        draft.workflow_slots.extend(
            slot for slot in workflow_slots(runninghub.provider_key, video_spec.spec_key)
            if slot.slot_key not in existing_slot_keys
        )
        first_frame = next(slot for slot in draft.workflow_slots if slot.provider_workflow_id == "2069607607387639810")
        for row in first_frame.node_info_list:
            if row.value_source == "shot.negative_prompt":
                row.required = False
        for slot in draft.workflow_slots:
            if slot.provider_workflow_id in TARGET_SLOT_NAMES:
                slot.display_name = TARGET_SLOT_NAMES[slot.provider_workflow_id]
        for model in draft.models:
            if model.agent_role == "director":
                model.output_schema_version = "shot-plan.v4"
                model.prompt_contract_version = "director-prompt.v6"
        if draft.pricing is None:
            raise RuntimeError("The current configuration has no test pricing catalog.")
        for slot in draft.workflow_slots:
            if slot.slot_key not in {rule.workflow_slot_key for rule in draft.pricing.rules}:
                draft.pricing.rules.append(PricingRuleDraft.model_validate({
                    "workflow_slot_key": slot.slot_key,
                    "unit": "runtime_second",
                    "unit_price": "0.001",
                    "minimum_charge": "0",
                    "estimated_runtime_seconds": "300" if "video" in slot.operation_kind else "60",
                }))
        draft = draft.model_validate(draft.model_dump(mode="json"))
        revised = revise_configuration(session, config, ReviseConfiguration(
            command_id=f"workflow-expand-revise-{uuid.uuid4()}",
            actor_id="local-user",
            expected_row_version=config.row_version,
            configuration=draft,
        ))
        config = session.get(ProductionConfigVersion, revised["id"])
        assert config is not None
        validated = validate_configuration(session, config, ValidateConfiguration(
            command_id=f"workflow-expand-validate-{uuid.uuid4()}",
            actor_id="local-user",
            expected_row_version=config.row_version,
        ))
        if validated["status"] != "ready":
            raise RuntimeError(f"Configuration validation failed: {validated['validation_report']}")
        config = session.get(ProductionConfigVersion, validated["id"])
        assert config is not None
        published = publish_configuration(session, config, PublishConfiguration(
            command_id=f"workflow-expand-publish-{uuid.uuid4()}",
            actor_id="local-user",
            expected_row_version=config.row_version,
            confirm_high_risk_changes=True,
        ))
        source = session.get(ProductionConfigVersion, source.id)
        assert source is not None
        retire_configuration(session, source, RetireConfiguration(
            command_id=f"workflow-expand-retire-{uuid.uuid4()}",
            actor_id="local-user",
            expected_row_version=source.row_version,
            confirm_reference_impact=True,
        ))
        print(f"Published: {published['id']} v{published['version_number']}")


if __name__ == "__main__":
    main()
