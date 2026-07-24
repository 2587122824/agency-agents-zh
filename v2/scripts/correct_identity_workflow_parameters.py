from __future__ import annotations

import uuid

from v2.backend.app.configuration.contracts import (
    CloneConfiguration,
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


IDENTITY_WORKFLOW_ID = "2073414172825706497"
IDENTITY_DENOISE_NODE_ID = "24"
IDENTITY_DENOISE_FIELD_PATH = "denoise"
IDENTITY_DENOISE_VALUE_SOURCE = "literal:1.0"


def correct_identity_denoise(draft) -> bool:
    slots = [
        slot
        for slot in draft.workflow_slots
        if slot.provider_workflow_id == IDENTITY_WORKFLOW_ID
    ]
    if len(slots) != 1:
        raise RuntimeError(
            f"Expected exactly one identity workflow {IDENTITY_WORKFLOW_ID}; found {len(slots)}."
        )

    bindings = [
        row
        for row in slots[0].node_info_list
        if row.node_id == IDENTITY_DENOISE_NODE_ID
        and row.field_path == IDENTITY_DENOISE_FIELD_PATH
    ]
    if len(bindings) != 1:
        raise RuntimeError(
            "Expected exactly one identity workflow denoise binding at node 24."
        )

    binding = bindings[0]
    if binding.value_type != "number":
        raise RuntimeError("The identity workflow denoise binding must be numeric.")
    if binding.value_source == IDENTITY_DENOISE_VALUE_SOURCE:
        return False
    binding.value_source = IDENTITY_DENOISE_VALUE_SOURCE
    return True


def main() -> None:
    with SessionLocal() as session:
        source = (
            session.query(ProductionConfigVersion)
            .filter(ProductionConfigVersion.status == "published")
            .order_by(
                ProductionConfigVersion.published_at.desc(),
                ProductionConfigVersion.id.desc(),
            )
            .first()
        )
        if source is None:
            raise RuntimeError("No published source configuration was found.")

        repository = SqlAlchemyConfigurationRepository(session)
        source_draft = _draft_from_config(repository, source, source.display_name)
        if not correct_identity_denoise(source_draft):
            print(f"Already published: {source.id} v{source.version_number}")
            return

        cloned = clone_configuration(
            session,
            source,
            CloneConfiguration(
                command_id=f"identity-denoise-clone-{uuid.uuid4()}",
                actor_id="local-user",
                display_name=source.display_name,
            ),
        )
        config = session.get(ProductionConfigVersion, cloned["id"])
        assert config is not None
        draft = _draft_from_config(repository, config, config.display_name)
        if not correct_identity_denoise(draft):
            raise RuntimeError("The cloned configuration did not require correction.")

        revised = revise_configuration(
            session,
            config,
            ReviseConfiguration(
                command_id=f"identity-denoise-revise-{uuid.uuid4()}",
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
                command_id=f"identity-denoise-validate-{uuid.uuid4()}",
                actor_id="local-user",
                expected_row_version=config.row_version,
            ),
        )
        if validated["status"] != "ready":
            raise RuntimeError(
                f"Configuration validation failed: {validated['validation_report']}"
            )

        config = session.get(ProductionConfigVersion, validated["id"])
        assert config is not None
        published = publish_configuration(
            session,
            config,
            PublishConfiguration(
                command_id=f"identity-denoise-publish-{uuid.uuid4()}",
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
                command_id=f"identity-denoise-retire-{uuid.uuid4()}",
                actor_id="local-user",
                expected_row_version=source.row_version,
                confirm_reference_impact=True,
            ),
        )
        print(f"Published: {published['id']} v{published['version_number']}")


if __name__ == "__main__":
    main()
