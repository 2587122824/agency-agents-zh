from __future__ import annotations

from v2.backend.app.providers import EnvironmentCredentialResolver, ProviderExecutionRequest
from v2.backend.app.providers.registry import default_provider_registry


def test_provider_registry_resolves_only_exact_registered_work_kind() -> None:
    registry = default_provider_registry()
    assert registry.get("runninghub") is None
    assert registry.resolve("runninghub", "generate_keyframe") is None
    assert registry.resolve("local", "generate_keyframe") is None
    local = registry.resolve("local", "assemble_timeline_contract")
    assert local is not None
    response = local.execute(ProviderExecutionRequest(
        work_kind="assemble_timeline_contract",
        request_fingerprint="a" * 64,
        request_manifest={"adapter_kind": "local"},
        parent_work_item_ids=("work_1", "work_2"),
    ))
    assert response == {
        "schema_version": "timeline-contract-result.v1",
        "result": "contract_assembled",
        "input_work_item_ids": ["work_1", "work_2"],
        "media_created": False,
    }


def test_environment_credential_resolver_requires_exact_allowlist_and_never_infers() -> None:
    environment = {"PROVIDER_API_KEY": "server-secret"}
    denied = EnvironmentCredentialResolver(environment, set())
    assert denied.resolve(None).state == "not_configured"
    assert denied.resolve("secret://provider/key").state == "unsupported_reference"
    assert denied.resolve("env://provider_api_key").state == "unsupported_reference"
    assert denied.resolve("env://PROVIDER_API_KEY").state == "not_authorized"

    allowed = EnvironmentCredentialResolver(environment, {"PROVIDER_API_KEY", "MISSING_KEY"})
    available = allowed.resolve("env://PROVIDER_API_KEY")
    assert available.state == "available"
    assert available.available is True
    assert available.secret == "server-secret"
    assert "server-secret" not in repr(available)
    assert allowed.resolve("env://MISSING_KEY").state == "missing"
