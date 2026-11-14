"""Security and secret redaction utilities.

Provides deterministic redaction of API keys, bearer tokens, passwords, and configured secrets
from command outputs, tool inputs, transcripts, and storage records.
"""

import re
from typing import List, Optional

# Standard regex patterns to detect API keys or bearer tokens in command output and storage records
SECRET_PATTERNS: List[re.Pattern] = [
    re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{15,}", re.IGNORECASE),
    re.compile(r"Basic\s+[a-zA-Z0-9+/=]{16,}", re.IGNORECASE),
    re.compile(r"Authorization\s*:\s*[^\r\n]{8,}", re.IGNORECASE),
    re.compile(r"(api[_-]?key|secret[_-]?key|access[_-]?token)\s*[:=]\s*['\"]?[a-zA-Z0-9_\-\.]{16,}['\"]?", re.IGNORECASE),
    re.compile(r"\bsk-[a-zA-Z0-9_\-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[a-zA-Z0-9]{15,}\b"),
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
            else:
                sanitized = sanitized[: m.start()] + "[REDACTED]" + sanitized[m.end() :]

    return sanitized
