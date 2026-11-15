"""Security and secret redaction utilities.

Provides defense-in-depth sanitization of recognized API keys, bearer tokens, passwords,
and configured secrets from command outputs, tool inputs, transcripts, and storage records.

NOTE: Regex-based redaction provides deterministic protection against common and standard
credential formats. It is a defense-in-depth measure and does not guarantee the detection
of novel, deeply obfuscated, or custom secret formats.
"""

import json
import re
from typing import Any, List, Optional

# Standard regex patterns to detect recognized API keys, bearer tokens, and credentials
SECRET_PATTERNS: List[re.Pattern] = [
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{15,}", re.IGNORECASE),
    re.compile(r"Basic\s+[a-zA-Z0-9+/=]{16,}", re.IGNORECASE),
    re.compile(r"Authorization\s*:\s*[^\r\n]{8,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key|secret[_-]?key|access[_-]?token|password)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-\.]{12,}['\"]?", re.IGNORECASE),
    re.compile(r"\bsk-[a-zA-Z0-9_\-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[a-zA-Z0-9]{15,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"\beyJ[a-zA-Z0-9_\-]{10,}\.eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]+\b"),
    re.compile(r"https?://[a-zA-Z0-9_\-]+:([^@\s]+)@[a-zA-Z0-9_\-\.]+"),
    re.compile(r"-----BEGIN [A-Z ]+PRIVATE KEY-----[\s\S]*?-----END [A-Z ]+PRIVATE KEY-----"),
]


def redact_secrets(text: str, extra_secrets: Optional[List[str]] = None) -> str:
    """Scan and redact known secrets and standard token formats from text."""
    if not text:
        return text

    sanitized = text

    # Redact explicit configured secrets
    if extra_secrets:
        for secret in extra_secrets:
            if secret and len(secret) >= 8 and secret in sanitized:
                sanitized = sanitized.replace(secret, "[REDACTED_SECRET]")

    # Redact standard bearer / auth token formats
    for pattern in SECRET_PATTERNS:
        matches = list(pattern.finditer(sanitized))
        for m in reversed(matches):
            matched_str = m.group(0)
            if matched_str.startswith("Bearer "):
                sanitized = sanitized[: m.start()] + "Bearer [REDACTED_TOKEN]" + sanitized[m.end() :]
            elif matched_str.startswith("Basic "):
                sanitized = sanitized[: m.start()] + "Basic [REDACTED_TOKEN]" + sanitized[m.end() :]
            elif matched_str.startswith("http://") or matched_str.startswith("https://"):
                # URL with embedded password
                sanitized = sanitized[: m.start(1)] + "[REDACTED_PASSWORD]" + sanitized[m.end(1) :]
            else:
                sanitized = sanitized[: m.start()] + "[REDACTED]" + sanitized[m.end() :]

    return sanitized


def redact_secrets_obj(data: Any, extra_secrets: Optional[List[str]] = None) -> Any:
    """Recursively scrub secrets from nested dictionaries, lists, or primitive values."""
    if isinstance(data, str):
        return redact_secrets(data, extra_secrets=extra_secrets)
    if isinstance(data, dict):
        return {
            k: redact_secrets_obj(v, extra_secrets=extra_secrets)
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact_secrets_obj(item, extra_secrets=extra_secrets) for item in data]
    return data
