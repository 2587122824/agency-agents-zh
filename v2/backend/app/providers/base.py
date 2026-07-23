from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class ProviderExecutionRequest:
    work_kind: str
    request_fingerprint: str
    request_manifest: dict[str, Any]
    parent_work_item_ids: tuple[str, ...] = ()
    parent_outputs: tuple[dict[str, Any], ...] = ()


class ProviderAdapterError(RuntimeError):
    def __init__(
        self,
        code: str,
        detail: str,
        response_manifest: dict[str, Any] | None = None,
    ):
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.response_manifest = response_manifest


class ProviderAdapter(Protocol):
    adapter_kind: str
    display_name: str
    external: bool
    execution_enabled: bool
    requires_credential: bool
    supported_work_kinds: frozenset[str]

    def execute(self, request: ProviderExecutionRequest) -> dict[str, Any]: ...


@dataclass(frozen=True)
class ProviderSubmission:
    provider_task_id: str
    response_manifest: dict[str, Any]


@dataclass(frozen=True)
class ProviderPollResult:
    state: Literal["running", "succeeded", "failed"]
    response_manifest: dict[str, Any]
    error_code: str | None = None
    error_detail: str | None = None


@runtime_checkable
class ExternalProviderAdapter(ProviderAdapter, Protocol):
    def submit(self, request: ProviderExecutionRequest) -> ProviderSubmission: ...

    def poll(self, request: ProviderExecutionRequest, provider_task_id: str) -> ProviderPollResult: ...
