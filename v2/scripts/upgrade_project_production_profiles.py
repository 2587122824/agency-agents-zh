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
from v2.backend.app.creation.agent_gateway import (
    CREATIVE_INPUT_CONTRACT_VERSION,
    CREATIVE_PROMPT_CONTRACT_VERSION,
)
from v2.backend.app.db.models import ProductionConfigVersion
from v2.backend.app.db.session import SessionLocal
from v2.backend.app.editor.agent_gateway import (
    EDITOR_INPUT_CONTRACT_VERSION,
    EDITOR_PROMPT_CONTRACT_VERSION,
)
from v2.backend.app.planning.agent_gateway import (
    CONTENT_PLANNER_INPUT_CONTRACT_VERSION,
    CONTENT_PLANNER_PROMPT_CONTRACT_VERSION,
)
from v2.backend.app.planning.director_gateway import (
    DIRECTOR_INPUT_CONTRACT_VERSION,
    DIRECTOR_PROMPT_CONTRACT_VERSION,
)
from v2.backend.app.production.agent_gateway import (
    PRODUCTION_PLANNER_INPUT_CONTRACT_VERSION,
    PRODUCTION_PLANNER_PROMPT_CONTRACT_VERSION,
)
from v2.backend.app.repositories import SqlAlchemyConfigurationRepository


CONTRACTS = {
    "creative": (CREATIVE_INPUT_CONTRACT_VERSION, CREATIVE_PROMPT_CONTRACT_VERSION),
    "planner": (CONTENT_PLANNER_INPUT_CONTRACT_VERSION, CONTENT_PLANNER_PROMPT_CONTRACT_VERSION),
    "director": (DIRECTOR_INPUT_CONTRACT_VERSION, DIRECTOR_PROMPT_CONTRACT_VERSION),
    "production_planner": (
        PRODUCTION_PLANNER_INPUT_CONTRACT_VERSION,
        PRODUCTION_PLANNER_PROMPT_CONTRACT_VERSION,
    ),
    "editor": (EDITOR_INPUT_CONTRACT_VERSION, EDITOR_PROMPT_CONTRACT_VERSION),
}


def apply_contracts(draft) -> bool:
    found: set[str] = set()
    changed = False
    for model in draft.models:
        expected = CONTRACTS.get(model.agent_role)
        if expected is None:
            continue
        found.add(model.agent_role)
        input_contract, prompt_contract = expected
        if model.input_contract_version != input_contract:
            model.input_contract_version = input_contract
            changed = True
        if model.prompt_contract_version != prompt_contract:
            model.prompt_contract_version = prompt_contract
            changed = True
    missing = sorted(set(CONTRACTS) - found)
    if missing:
        raise RuntimeError(f"Published configuration is missing required agent roles: {missing}")
    return changed


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
        if not apply_contracts(source_draft):
            print(f"Already published: {source.id} v{source.version_number}")
            return

        cloned = clone_configuration(
            session,
            source,
            CloneConfiguration(
                command_id=f"production-profile-clone-{uuid.uuid4()}",
                actor_id="local-user",
                display_name=source.display_name,
            ),
        )
        config = session.get(ProductionConfigVersion, cloned["id"])
        assert config is not None
        draft = _draft_from_config(repository, config, config.display_name)
        if not apply_contracts(draft):
            raise RuntimeError("The cloned configuration did not require a contract upgrade.")
        revised = revise_configuration(
            session,
            config,
            ReviseConfiguration(
                command_id=f"production-profile-revise-{uuid.uuid4()}",
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
                command_id=f"production-profile-validate-{uuid.uuid4()}",
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
                command_id=f"production-profile-publish-{uuid.uuid4()}",
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
                command_id=f"production-profile-retire-{uuid.uuid4()}",
                actor_id="local-user",
                expected_row_version=source.row_version,
                confirm_reference_impact=True,
            ),
        )
        print(f"Published: {published['id']} v{published['version_number']}")


if __name__ == "__main__":
    main()
