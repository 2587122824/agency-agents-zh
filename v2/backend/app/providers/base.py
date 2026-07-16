from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderExecutionRequest:
    work_kind: str
    request_fingerprint: str
    request_manifest: dict[str, Any]
    parent_work_item_ids: tuple[str, ...] = ()


class ProviderAdapterError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


class ProviderAdapter(Protocol):
    adapter_kind: str
    display_name: str
    external: bool
    requires_credential: bool
    supported_work_kinds: frozenset[str]

    def execute(self, request: ProviderExecutionRequest) -> dict[str, Any]: ...
