"""Provider adapter boundary.

Concrete providers must implement explicit V2 contracts. This package does not
import V1 adapters and contains no provider selection or downgrade behavior.
"""

from .base import ProviderAdapter, ProviderAdapterError, ProviderExecutionRequest
from .credentials import CredentialResolution, EnvironmentCredentialResolver
from .registry import ProviderAdapterRegistry, default_provider_registry

__all__ = [
    "CredentialResolution",
    "EnvironmentCredentialResolver",
    "ProviderAdapter",
    "ProviderAdapterError",
    "ProviderAdapterRegistry",
    "ProviderExecutionRequest",
    "default_provider_registry",
]
