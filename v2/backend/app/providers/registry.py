from __future__ import annotations

from collections.abc import Iterable
import os

from .base import ProviderAdapter
from .builtin import LocalSubtitleAdapter, LocalTimelineAdapter, MockProviderAdapter
from .cosyvoice import CosyVoiceAdapter
from .runninghub import RunningHubAdapter


class ProviderAdapterRegistry:
    def __init__(self, adapters: Iterable[ProviderAdapter] = ()) -> None:
        self._adapters: dict[str, ProviderAdapter] = {}
        for adapter in adapters:
            self.register(adapter)

    def register(self, adapter: ProviderAdapter) -> None:
        adapter_kind = adapter.adapter_kind.strip()
        if not adapter_kind:
            raise ValueError("Provider adapter kind must not be blank.")
        if adapter_kind in self._adapters:
            raise ValueError(f"Provider adapter {adapter_kind!r} is already registered.")
        self._adapters[adapter_kind] = adapter

    def get(self, adapter_kind: str | None) -> ProviderAdapter | None:
        return self._adapters.get(str(adapter_kind or "").strip())

    def resolve(self, adapter_kind: str | None, work_kind: str) -> ProviderAdapter | None:
        adapter = self.get(adapter_kind)
        if not adapter or work_kind not in adapter.supported_work_kinds:
            return None
        return adapter


def default_provider_registry() -> ProviderAdapterRegistry:
    external_enabled = os.getenv("V2_EXTERNAL_PROVIDER_EXECUTION_ENABLED", "").strip().lower() in {"1", "true", "yes"}
    return ProviderAdapterRegistry((
        MockProviderAdapter(),
        LocalTimelineAdapter(),
        LocalSubtitleAdapter(),
        RunningHubAdapter(execution_enabled=external_enabled),
        CosyVoiceAdapter(execution_enabled=external_enabled),
    ))
