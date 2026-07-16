from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from .credentials import CredentialState


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
    credential_required: bool | None
    credential_state: CredentialState
    status: Literal["connected", "adapter_not_connected", "credential_not_ready"]


class ProviderReadinessView(BaseModel):
    network_probe_performed: bool
    external_execution_enabled: bool
    providers: list[ProviderReadinessItem]
