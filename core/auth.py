"""Authentication, role-based access control, and principal resolution.

Implements security principals and role authorization for the runtime API.
Enforces that:
1. Unauthenticated callers cannot execute privileged actions (e.g. custom verification).
2. Authenticated non-operators are denied privileged actions (HTTP 403 Forbidden).
3. Authenticated operators have their actions attributed to their verified principal identity.
"""

from enum import Enum
import logging
from typing import Dict, Optional
from pydantic import BaseModel
from fastapi import Header, HTTPException, status

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


# Standard pre-configured token map for local runtime authentication
# In production, this integrates with external OAuth2/OIDC/API-key providers
DEFAULT_PRINCIPAL_TOKENS: Dict[str, Principal] = {
    "op-secret-token-3891": Principal(identity="operator_admin", role=PrincipalRole.OPERATOR),
    "dev-operator-token": Principal(identity="dev_operator", role=PrincipalRole.OPERATOR),
    "viewer-token-9912": Principal(identity="guest_viewer", role=PrincipalRole.VIEWER),
    "agent-internal-token": Principal(identity="agent_loop", role=PrincipalRole.AGENT),
}


def authenticate_principal(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_api_key: Optional[str] = Header(None, alias="X-API-Key"),
) -> Optional[Principal]:
    """Resolve and validate caller credentials against recognized principals.

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

    if token in DEFAULT_PRINCIPAL_TOKENS:
        return DEFAULT_PRINCIPAL_TOKENS[token]

    return None
