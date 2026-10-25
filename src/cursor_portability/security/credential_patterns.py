from __future__ import annotations

PATTERN_VERSION = 1

_SUBSTRINGS = (
    "token",
    "secret",
    "password",
    "passwd",
    "apikey",
    "api_key",
    "credential",
    "cookie",
    "auth",
    "bearer",
    "private_key",
    "machineid",
    "devdeviceid",
    "sqmid",
)


def is_credential_key(key: str) -> bool:
    if key.startswith("secret://"):
        return True
    lower = key.lower()
    return any(s in lower for s in _SUBSTRINGS)


def pattern_id_for_key(key: str) -> str | None:
    if key.startswith("secret://"):
        return "secret_prefix"
    lower = key.lower()
    for s in _SUBSTRINGS:
        if s in lower:
            return f"substr:{s}"
    return None
