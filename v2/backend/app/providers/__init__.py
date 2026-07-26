"""Provider adapter boundary.

Concrete providers must implement explicit V2 contracts. This package does not
import V1 adapters and contains no provider selection or downgrade behavior.
"""

from .base import (
    ExternalProviderAdapter,
    ProviderAdapter,
    ProviderAdapterError,
    ProviderExecutionRequest,
    ProviderPollResult,
    ProviderSubmission,
)
from .registry import ProviderAdapterRegistry, default_provider_registry
from .cosyvoice import CosyVoiceAdapter, CosyVoiceTransport, HttpxCosyVoiceTransport
from .runninghub import HttpxRunningHubTransport, RunningHubAdapter, RunningHubTransport

__all__ = [
    "ExternalProviderAdapter",
    "ProviderAdapter",
    "ProviderAdapterError",
    "ProviderAdapterRegistry",
    "ProviderExecutionRequest",
    "ProviderPollResult",
    "ProviderSubmission",
    "CosyVoiceAdapter",
    "CosyVoiceTransport",
    "HttpxCosyVoiceTransport",
    "RunningHubAdapter",
    "RunningHubTransport",
    "HttpxRunningHubTransport",
    "default_provider_registry",
]
