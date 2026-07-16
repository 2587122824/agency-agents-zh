from __future__ import annotations

from sqlalchemy.orm import Session

from ..repositories import SqlAlchemyConfigurationRepository
from .credentials import EnvironmentCredentialResolver
from .registry import ProviderAdapterRegistry, default_provider_registry


def provider_readiness(
    session: Session,
    *,
    registry: ProviderAdapterRegistry | None = None,
    credential_resolver: EnvironmentCredentialResolver | None = None,
) -> dict:
    repository = SqlAlchemyConfigurationRepository(session)
    adapter_registry = registry or default_provider_registry()
    resolver = credential_resolver or EnvironmentCredentialResolver.from_environment()
    rows: list[dict] = []
    for configuration in repository.configurations():
        if configuration.status != "published":
            continue
        providers = repository.component_rows(configuration.id)["provider"]
        for provider in providers:
            adapter = adapter_registry.get(provider.adapter_kind)
            credential = resolver.resolve(provider.credential_ref)
            if adapter is None:
                status = "adapter_not_connected"
            elif adapter.external and not adapter.execution_enabled:
                status = "execution_disabled"
            elif adapter.requires_credential and not credential.available:
                status = "credential_not_ready"
            else:
                status = "connected"
            rows.append({
                "configuration_version_id": configuration.id,
                "configuration_display_name": configuration.display_name,
                "configuration_version_number": configuration.version_number,
                "provider_version_id": provider.id,
                "provider_display_name": provider.display_name,
                "adapter_kind": provider.adapter_kind,
                "capabilities": list(provider.capabilities or []),
                "adapter_registered": adapter is not None,
                "external": adapter.external if adapter else None,
                "execution_enabled": adapter.execution_enabled if adapter else None,
                "credential_required": adapter.requires_credential if adapter else None,
                "credential_state": credential.state,
                "status": status,
            })
    return {
        "network_probe_performed": False,
        "external_execution_enabled": any(
            row["status"] == "connected" and row["external"] is True
            for row in rows
        ),
        "providers": rows,
    }
