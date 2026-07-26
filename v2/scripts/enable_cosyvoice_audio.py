from __future__ import annotations

import uuid

from v2.backend.app.configuration.contracts import (
    CloneConfiguration,
    NodeBinding,
    PricingRuleDraft,
    ProviderDraft,
    PublishConfiguration,
    RetireConfiguration,
    ReviseConfiguration,
    ValidateConfiguration,
    VoicePresetDraft,
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


PROVIDER_KEY = "dashscope-cosyvoice"
SLOT_KEY = "cosyvoice-voiceover-wav"
VOICE_PRESETS = [
    VoicePresetDraft(key="warm_female", display_name="温暖女声", provider_voice_id="longxiaochun", description="自然、温暖，适合品牌旁白", preview_text="欢迎来到片场 V2 配音试听。"),
    VoicePresetDraft(key="bright_female", display_name="明亮女声", provider_voice_id="longxiaoxia", description="清晰、明快，适合产品介绍", preview_text="欢迎来到片场 V2 配音试听。"),
    VoicePresetDraft(key="steady_male", display_name="沉稳男声", provider_voice_id="longxiaocheng", description="稳定、可信，适合解说", preview_text="欢迎来到片场 V2 配音试听。"),
    VoicePresetDraft(key="youthful", display_name="青春声线", provider_voice_id="longxiaobai", description="轻快、有活力，适合短视频", preview_text="欢迎来到片场 V2 配音试听。"),
    VoicePresetDraft(key="friendly_male", display_name="亲和男声", provider_voice_id="longlaotie", description="亲切、口语化，适合生活内容", preview_text="欢迎来到片场 V2 配音试听。"),
]


def enable_cosyvoice(draft) -> bool:
    if any(provider.adapter_kind == "cosyvoice" for provider in draft.providers):
        return False
    draft.providers.append(ProviderDraft(
        provider_key=PROVIDER_KEY,
        display_name="阿里云 CosyVoice",
        adapter_kind="cosyvoice",
        region="cn-beijing",
        base_url="https://dashscope.aliyuncs.com",
        api_key=None,
        capabilities=["tts"],
        request_timeout_seconds=600,
        poll_interval_seconds=5,
        max_concurrency=1,
    ))
    draft.workflow_slots.append(WorkflowSlotDraft(
        slot_key=SLOT_KEY,
        display_name="CosyVoice 暖声女声旁白（WAV）",
        operation_kind="tts",
        provider_key=PROVIDER_KEY,
        provider_workflow_id="cosyvoice-v1",
        provider_workflow_version="v1",
        model_config_key=None,
        input_schema_version="cosyvoice-tts-input.v2",
        output_schema_version="cosyvoice-wav-output.v1",
        node_info_list=[
            NodeBinding(node_id="input", field_path="text", value_source="input_contract.voiceover_text", value_type="string"),
            NodeBinding(node_id="input", field_path="voice", value_source="input_contract.voice.provider_voice_id", value_type="string"),
            NodeBinding(node_id="input", field_path="rate", value_source="input_contract.speaking_rate", value_type="number"),
            NodeBinding(node_id="input", field_path="volume", value_source="input_contract.volume", value_type="integer"),
            NodeBinding(node_id="input", field_path="format", value_source="literal:wav", value_type="string"),
            NodeBinding(node_id="input", field_path="sample_rate", value_source="literal:24000", value_type="integer"),
        ],
        supported_video_spec_keys=[],
        capability_tags=["voiceover", "wav", "preset_voice"],
    ))
    draft.audio.supported_modes = ["off", "voiceover"]
    draft.audio.tts_workflow_slot_key = SLOT_KEY
    draft.audio.sample_rate = 24000
    draft.audio.channels = 1
    draft.audio.format = "wav"
    draft.audio.voice_presets = VOICE_PRESETS
    draft.audio.default_voice_key = "warm_female"
    draft.audio.speaking_rate_default = 1.0
    draft.audio.volume_min = 0
    draft.audio.volume_max = 100
    draft.audio.volume_default = 50
    draft.audio.duration_tolerance_ms = 1500
    if "audio/wav" not in draft.storage.allowed_mime_types:
        draft.storage.allowed_mime_types.append("audio/wav")
    if draft.pricing is None:
        raise RuntimeError("The active development configuration has no pricing catalog.")
    draft.pricing.rules.append(PricingRuleDraft(
        workflow_slot_key=SLOT_KEY,
        unit="output_second",
        unit_price="0.001",
        minimum_charge=None,
        estimated_runtime_seconds=None,
    ))
    return True


def main() -> None:
    with SessionLocal() as session:
        source = (
            session.query(ProductionConfigVersion)
            .filter(ProductionConfigVersion.status == "published")
            .order_by(ProductionConfigVersion.published_at.desc(), ProductionConfigVersion.id.desc())
            .first()
        )
        if source is None:
            raise RuntimeError("No published source configuration was found.")
        repository = SqlAlchemyConfigurationRepository(session)
        source_draft = _draft_from_config(repository, source, source.display_name)
        if not enable_cosyvoice(source_draft):
            print(f"Already published: {source.id} v{source.version_number}")
            return
        cloned = clone_configuration(
            session,
            source,
            CloneConfiguration(
                command_id=f"cosyvoice-clone-{uuid.uuid4()}",
                actor_id="local-user",
                display_name=source.display_name,
            ),
        )
        config = session.get(ProductionConfigVersion, cloned["id"])
        assert config is not None
        draft = _draft_from_config(repository, config, config.display_name)
        if not enable_cosyvoice(draft):
            raise RuntimeError("The cloned configuration already contains CosyVoice.")
        revised = revise_configuration(
            session,
            config,
            ReviseConfiguration(
                command_id=f"cosyvoice-revise-{uuid.uuid4()}",
                actor_id="local-user",
                expected_row_version=config.row_version,
                configuration=draft,
            ),
        )
        config = session.get(ProductionConfigVersion, revised["id"])
        assert config is not None
        validated = validate_configuration(
            session,
            config,
            ValidateConfiguration(
                command_id=f"cosyvoice-validate-{uuid.uuid4()}",
                actor_id="local-user",
                expected_row_version=config.row_version,
            ),
        )
        if validated["status"] != "ready":
            raise RuntimeError(f"Configuration validation failed: {validated['validation_report']}")
        config = session.get(ProductionConfigVersion, validated["id"])
        assert config is not None
        published = publish_configuration(
            session,
            config,
            PublishConfiguration(
                command_id=f"cosyvoice-publish-{uuid.uuid4()}",
                actor_id="local-user",
                expected_row_version=config.row_version,
                confirm_high_risk_changes=True,
            ),
        )
        source = session.get(ProductionConfigVersion, source.id)
        assert source is not None
        retire_configuration(
            session,
            source,
            RetireConfiguration(
                command_id=f"cosyvoice-retire-{uuid.uuid4()}",
                actor_id="local-user",
                expected_row_version=source.row_version,
                confirm_reference_impact=True,
            ),
        )
        print(f"Published: {published['id']} v{published['version_number']}")


if __name__ == "__main__":
    main()
