from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Literal


CredentialState = Literal[
    "not_configured",
    "unsupported_reference",
    "not_authorized",
    "missing",
    "available",
]

_ENV_REFERENCE = re.compile(r"^env://([A-Z][A-Z0-9_]{1,127})$")


@dataclass(frozen=True)
class CredentialResolution:
    state: CredentialState
    secret: str | None = field(default=None, repr=False)

    @property
    def available(self) -> bool:
        return self.state == "available"


class EnvironmentCredentialResolver:
    def __init__(self, environment: Mapping[str, str], allowed_names: set[str]) -> None:
        self._environment = environment
        self._allowed_names = frozenset(allowed_names)

    @classmethod
    def from_environment(cls) -> EnvironmentCredentialResolver:
        allowed_names = {
            name.strip()
            for name in os.getenv("V2_CREDENTIAL_ENV_ALLOWLIST", "").split(",")
            if name.strip()
        }
        return cls(os.environ, allowed_names)

    def resolve(self, credential_ref: str | None) -> CredentialResolution:
        reference = str(credential_ref or "").strip()
        if not reference:
            return CredentialResolution("not_configured")
        match = _ENV_REFERENCE.fullmatch(reference)
        if not match:
            return CredentialResolution("unsupported_reference")
        name = match.group(1)
        if name not in self._allowed_names:
            return CredentialResolution("not_authorized")
        secret = str(self._environment.get(name, "")).strip()
        if not secret:
            return CredentialResolution("missing")
        return CredentialResolution("available", secret)
