from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .base import ProviderAdapterError, ProviderExecutionRequest


@dataclass(frozen=True)
class MockProviderAdapter:
    adapter_kind: str = "mock"
    display_name: str = "Mock"
    external: bool = False
    requires_credential: bool = False
    supported_work_kinds: frozenset[str] = frozenset({
        "generate_keyframe",
        "generate_i2v_clip",
        "generate_tts",
        "assemble_timeline_contract",
    })

    def execute(self, request: ProviderExecutionRequest) -> dict[str, Any]:
        return {
            "schema_version": "mock-provider-response.v1",
            "result": "simulated",
            "request_fingerprint": request.request_fingerprint,
            "media_created": False,
            "provider_task_id": None,
        }


@dataclass(frozen=True)
class LocalTimelineAdapter:
    adapter_kind: str = "local"
    display_name: str = "Local timeline contract"
    external: bool = False
    requires_credential: bool = False
    supported_work_kinds: frozenset[str] = frozenset({"assemble_timeline_contract"})

    def execute(self, request: ProviderExecutionRequest) -> dict[str, Any]:
        if request.work_kind not in self.supported_work_kinds:
            raise ProviderAdapterError(
                "PROVIDER_ADAPTER_NOT_CONNECTED",
                f"Adapter {self.adapter_kind!r} is not registered for work kind {request.work_kind!r}.",
            )
        return {
            "schema_version": "timeline-contract-result.v1",
            "result": "contract_assembled",
            "input_work_item_ids": list(request.parent_work_item_ids),
            "media_created": False,
        }
