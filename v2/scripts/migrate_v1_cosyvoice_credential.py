from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path

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


DEFAULT_V1_RUNTIME_CONFIG = Path(__file__).resolve().parents[2] / "tmp" / "web_runtime_voice_config.json"


def read_v1_api_key(path: Path) -> str:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError as exc:
        raise RuntimeError("V1 CosyVoice runtime credential file is missing.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("V1 CosyVoice runtime credential file is unreadable.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("V1 CosyVoice runtime credential must be a JSON object.")
    if str(payload.get("provider") or "").strip() != "aliyun_cosyvoice":
        raise RuntimeError("V1 runtime credential is not an Aliyun CosyVoice credential.")
    api_key = str(payload.get("api_key") or "").strip()
    if not api_key:
        raise RuntimeError("V1 CosyVoice API Key is missing.")
    return api_key


def apply_cosyvoice_credential(draft, api_key: str) -> bool:
    providers = [provider for provider in draft.providers if provider.adapter_kind == "cosyvoice"]
    if len(providers) != 1:
        raise RuntimeError("The published configuration must contain exactly one CosyVoice Provider.")
    provider = providers[0]
    current = str(provider.api_key or "").strip()
    if current:
        if current != api_key:
            raise RuntimeError("The published CosyVoice Provider already uses a different API Key.")
        return False
    provider.api_key = api_key
    return True


def migrate(runtime_config_path: Path) -> dict:
    api_key = read_v1_api_key(runtime_config_path)
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
        if not apply_cosyvoice_credential(source_draft, api_key):
            return {
                "changed": False,
                "id": source.id,
                "version_number": source.version_number,
                "api_key_state": "configured",
            }

        cloned = clone_configuration(
            session,
            source,
            CloneConfiguration(
                command_id=f"v1-cosyvoice-credential-clone-{uuid.uuid4()}",
                actor_id="local-user",
                display_name=source.display_name,
            ),
        )
        config = session.get(ProductionConfigVersion, cloned["id"])
        assert config is not None
        draft = _draft_from_config(repository, config, config.display_name)
        if not apply_cosyvoice_credential(draft, api_key):
            raise RuntimeError("The cloned configuration unexpectedly already contains the credential.")
        revised = revise_configuration(
            session,
            config,
            ReviseConfiguration(
                command_id=f"v1-cosyvoice-credential-revise-{uuid.uuid4()}",
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
                command_id=f"v1-cosyvoice-credential-validate-{uuid.uuid4()}",
                actor_id="local-user",
                expected_row_version=config.row_version,
            ),
        )
        if validated["status"] != "ready":
            raise RuntimeError("Configuration validation failed.")
        config = session.get(ProductionConfigVersion, validated["id"])
        assert config is not None
        published = publish_configuration(
            session,
            config,
            PublishConfiguration(
                command_id=f"v1-cosyvoice-credential-publish-{uuid.uuid4()}",
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
                command_id=f"v1-cosyvoice-credential-retire-{uuid.uuid4()}",
                actor_id="local-user",
                expected_row_version=source.row_version,
                confirm_reference_impact=True,
            ),
        )
        return {
            "changed": True,
            "id": published["id"],
            "version_number": published["version_number"],
            "api_key_state": "configured",
        }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate the V1 runtime CosyVoice credential into a new immutable V2 configuration version.",
    )
    parser.add_argument(
        "--runtime-config",
        type=Path,
        default=DEFAULT_V1_RUNTIME_CONFIG,
        help="Path to the V1 runtime voice credential JSON.",
    )
    args = parser.parse_args()
    result = migrate(args.runtime_config.resolve())
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
