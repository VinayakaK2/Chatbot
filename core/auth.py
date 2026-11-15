"""Authentication, role-based access control, and principal resolution.

Implements security principals and role authorization for the runtime API.
Enforces that:
1. Unauthenticated callers cannot execute privileged actions (e.g. custom verification).
2. Authenticated non-operators are denied privileged actions (HTTP 403 Forbidden).
3. Authenticated operators have their actions attributed to their verified principal identity.
4. Hardcoded development/test tokens are strictly rejected in production environments.
"""

from enum import Enum
import logging
from typing import Dict, List, Optional
from pydantic import BaseModel
from fastapi import Depends, Header, HTTPException, status

from core.config import Settings, get_settings

logger = logging.getLogger("agent_runtime.core.auth")


class PrincipalRole(str, Enum):
    """Categorical roles defining caller authority."""

    OPERATOR = "operator"
    VIEWER = "viewer"
    AGENT = "agent"


class Principal(BaseModel):
    """Authenticated entity identity and assigned security role."""

    identity: str
    role: PrincipalRole


# Standard pre-configured test/development token map.
# CRITICAL SECURITY INVARIANT:
# In production (settings.environment == "production"), these tokens are NEVER accepted.
# Production operator authentication requires explicit configuration via OPERATOR_API_TOKENS.
TEST_DEV_PRINCIPAL_TOKENS: Dict[str, Principal] = {
    "op-secret-token-3891": Principal(identity="operator_admin", role=PrincipalRole.OPERATOR),
    "dev-operator-token": Principal(identity="dev_operator", role=PrincipalRole.OPERATOR),
    "viewer-token-9912": Principal(identity="guest_viewer", role=PrincipalRole.VIEWER),
    "agent-internal-token": Principal(identity="agent_loop", role=PrincipalRole.AGENT),
}


def authenticate_principal(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
    settings: Settings = Depends(get_settings),
) -> Optional[Principal]:
    """Resolve and validate caller credentials against recognized principals.

    Security Boundaries:
    1. In production (environment == "production"):
       - Hardcoded test/dev tokens are strictly forbidden.
       - Operator credentials must be configured via environment settings (OPERATOR_API_TOKENS).
       - Fails closed if no operator tokens match or if test tokens are presented.
    2. In non-production (development / test):
       - Explicitly configured operator tokens take precedence.
       - Falls back to TEST_DEV_PRINCIPAL_TOKENS for automated test harnesses and local development.

    Returns:
        Principal if valid credentials are provided, or None if unauthenticated.
    """
    token: Optional[str] = None

    if authorization:
        parts = authorization.strip().split()
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1]
        elif len(parts) == 1:
            token = parts[0]

    if not token and x_api_key:
        token = x_api_key.strip()

    if not token:
        return None

    # 1. Match against explicitly configured operator tokens from settings/environment
    if settings.operator_api_tokens:
        for idx, op_token in enumerate(settings.operator_api_tokens):
            if token == op_token:
                return Principal(identity=f"operator_env_{idx+1}", role=PrincipalRole.OPERATOR)

    # 2. In production, fail-closed: NEVER accept hardcoded test/dev fallback credentials
    if settings.environment.lower() == "production":
        if token in TEST_DEV_PRINCIPAL_TOKENS:
            logger.warning("Rejected test/dev token attempt against production environment: test tokens are forbidden.")
        return None

    # 3. In development / test environments, accept test/dev credentials
    if token in TEST_DEV_PRINCIPAL_TOKENS:
        return TEST_DEV_PRINCIPAL_TOKENS[token]

    return None
