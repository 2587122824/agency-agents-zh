from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

class ProviderReadinessItem(BaseModel):
    configuration_version_id: str
    configuration_display_name: str
    configuration_version_number: int
    provider_version_id: str
    provider_display_name: str
    adapter_kind: str
    capabilities: list[str]
    adapter_registered: bool
    external: bool | None
    execution_enabled: bool | None
    credential_required: bool | None
    api_key_state: Literal["not_required", "missing", "configured"]
    configuration_ready: bool
    configuration_issue_count: int
    configuration_issue_codes: list[str]
    status: Literal[
        "connected",
        "adapter_not_connected",
        "configuration_not_ready",
        "execution_disabled",
        "credential_not_ready",
    ]
    next_action: Literal[
        "connect_adapter",
        "revise_configuration",
        "configure_credential",
        "enable_execution",
        "ready",
    ]


class ProviderReadinessView(BaseModel):
    network_probe_performed: bool
    external_execution_enabled: bool
    providers: list[ProviderReadinessItem]
