from __future__ import annotations

import uuid

from v2.backend.app.configuration.contracts import (
    CloneConfiguration,
    NodeBinding,
    PublishConfiguration,
    RetireConfiguration,
    ReviseConfiguration,
    ValidateConfiguration,
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
from v2.scripts.enable_cosyvoice_audio import SLOT_KEY, VOICE_PRESETS


def upgrade_audio_execution(draft) -> None:
    workflows = [slot for slot in draft.workflow_slots if slot.slot_key == SLOT_KEY]
    if len(workflows) != 1:
        raise RuntimeError("The published configuration must contain exactly one CosyVoice TTS slot.")
    workflow = workflows[0]
    workflow.input_schema_version = "cosyvoice-tts-input.v2"
    workflow.node_info_list = [
        NodeBinding(node_id="input", field_path="text", value_source="input_contract.voiceover_text", value_type="string"),
        NodeBinding(node_id="input", field_path="voice", value_source="input_contract.voice.provider_voice_id", value_type="string"),
        NodeBinding(node_id="input", field_path="rate", value_source="input_contract.speaking_rate", value_type="number"),
        NodeBinding(node_id="input", field_path="volume", value_source="input_contract.volume", value_type="integer"),
        NodeBinding(node_id="input", field_path="format", value_source="literal:wav", value_type="string"),
        NodeBinding(node_id="input", field_path="sample_rate", value_source="literal:24000", value_type="integer"),
    ]
    draft.audio.voice_presets = VOICE_PRESETS
    draft.audio.default_voice_key = "warm_female"
    draft.audio.speaking_rate_default = 1.0
    draft.audio.volume_min = 0
    draft.audio.volume_max = 100
    draft.audio.volume_default = 50
    draft.audio.duration_tolerance_ms = 1500


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
        cloned = clone_configuration(
            session,
            source,
            CloneConfiguration(
                command_id=f"audio-execution-clone-{uuid.uuid4()}",
                actor_id="local-user",
                display_name=source.display_name,
            ),
        )
        config = session.get(ProductionConfigVersion, cloned["id"])
        assert config is not None
        draft = _draft_from_config(repository, config, config.display_name)
        upgrade_audio_execution(draft)
        revised = revise_configuration(
            session,
            config,
            ReviseConfiguration(
                command_id=f"audio-execution-revise-{uuid.uuid4()}",
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
                command_id=f"audio-execution-validate-{uuid.uuid4()}",
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
                command_id=f"audio-execution-publish-{uuid.uuid4()}",
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
                command_id=f"audio-execution-retire-{uuid.uuid4()}",
                actor_id="local-user",
                expected_row_version=source.row_version,
                confirm_reference_impact=True,
            ),
        )
        print(f"Published: {published['id']} v{published['version_number']}")


if __name__ == "__main__":
    main()
