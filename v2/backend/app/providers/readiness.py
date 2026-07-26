from __future__ import annotations

from sqlalchemy.orm import Session

from ..repositories import SqlAlchemyConfigurationRepository
from .registry import ProviderAdapterRegistry, default_provider_registry
from .cosyvoice import cosyvoice_workflow_contract_issues
from .runninghub_contract import runninghub_workflow_contract_issues


def provider_readiness(
    session: Session,
    *,
    registry: ProviderAdapterRegistry | None = None,
) -> dict:
    repository = SqlAlchemyConfigurationRepository(session)
    adapter_registry = registry or default_provider_registry()
    rows: list[dict] = []
    for configuration in repository.configurations():
        if configuration.status != "published":
            continue
        components = repository.component_rows(configuration.id)
        providers = components["provider"]
        for provider in providers:
            adapter = adapter_registry.get(provider.adapter_kind)
            if adapter is not None and not adapter.requires_credential:
                api_key_state = "not_required"
            else:
                api_key_state = "configured" if str(provider.api_key or "").strip() else "missing"
            configuration_issues: list[dict] = []
            if provider.adapter_kind == "runninghub":
                for workflow in components["workflow_slot"]:
                    if workflow.provider_config_version_id != provider.id:
                        continue
                    configuration_issues.extend(
                        runninghub_workflow_contract_issues(
                            workflow.operation_kind,
                            workflow.node_info_list,
                        )
                    )
            if provider.adapter_kind == "cosyvoice":
                for workflow in components["workflow_slot"]:
                    if workflow.provider_config_version_id == provider.id:
                        configuration_issues.extend(cosyvoice_workflow_contract_issues(workflow.node_info_list or []))
            configuration_ready = not configuration_issues
            if adapter is None:
                status = "adapter_not_connected"
                next_action = "connect_adapter"
            elif not configuration_ready:
                status = "configuration_not_ready"
                next_action = "revise_configuration"
            elif adapter.requires_credential and api_key_state != "configured":
                status = "credential_not_ready"
                next_action = "configure_credential"
            elif adapter.external and not adapter.execution_enabled:
                status = "execution_disabled"
                next_action = "enable_execution"
            else:
                status = "connected"
                next_action = "ready"
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
                "api_key_state": api_key_state,
                "configuration_ready": configuration_ready,
                "configuration_issue_count": len(configuration_issues),
                "configuration_issue_codes": sorted({str(issue["code"]) for issue in configuration_issues}),
                "status": status,
                "next_action": next_action,
            })
    return {
        "network_probe_performed": False,
        "external_execution_enabled": any(
            row["status"] == "connected" and row["external"] is True
            for row in rows
        ),
        "providers": rows,
    }
