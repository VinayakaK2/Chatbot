"""Deny-first permission policy engine and command risk classification.

Implements Section 5 of 2604.14228v3.pdf and Phase 5 of docs/phases.md:
- Precedence rule: DENY > ASK > ALLOW
- Hard-deny rules for destructive operations, system manipulation, and credential dumping
- Ask-policy rules for development, testing, and build commands
- Fail-closed default for unclassified or obfuscated commands
"""

from enum import Enum
import logging
import re
from typing import Any, Dict, List, Optional, Pattern, Set, Tuple
from pydantic import BaseModel, ConfigDict, Field

from core.tool_contracts import ToolDefinition, ToolRiskCategory

logger = logging.getLogger("agent_runtime.core.permissions")


class PermissionDecision(str, Enum):
    """Categorical permission evaluation outcomes enforcing defense-in-depth."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


# ==============================================================================
# Hard-Deny Patterns (Prohibited System and Destructive Operations)
# ==============================================================================

# Destructive storage and volume operations
STORAGE_DESTRUCTIVE_PATTERNS: List[Pattern[str]] = [
    re.compile(r"\bformat\b(\s+([a-zA-Z]:|\/))?", re.IGNORECASE),
    re.compile(r"\bformat-volume\b", re.IGNORECASE),
    re.compile(r"\bdiskpart\b", re.IGNORECASE),
    re.compile(r"\bfsutil\b", re.IGNORECASE),
    re.compile(r"\bbcdedit\b", re.IGNORECASE),
]

# System-level root destruction and indiscriminate deletion
SYSTEM_ROOT_DESTRUCTION_PATTERNS: List[Pattern[str]] = [
    # rmdir /s /q on root or system dirs
    re.compile(r"\brmdir\s+.*(/s|/q).*[a-zA-Z]:\\", re.IGNORECASE),
    re.compile(r"\brmdir\s+.*(/s|/q).*\b(windows|system32|program\s*files)\b", re.IGNORECASE),
    # Remove-Item -Recurse -Force on drive root or windows dirs
    re.compile(r"\bremove-item\b.*-recurse.*[a-zA-Z]:(\\|\/)", re.IGNORECASE),
    re.compile(r"\bremove-item\b.*-recurse.*\b(windows|system32|program\s*files)\b", re.IGNORECASE),
    re.compile(r"\bdel\s+.*(/s|/f|/q).*[a-zA-Z]:\\", re.IGNORECASE),
]

# Windows Registry manipulation
REGISTRY_MANIPULATION_PATTERNS: List[Pattern[str]] = [
    re.compile(r"\breg\s+(add|delete|import|restore|copy|save)\b", re.IGNORECASE),
    re.compile(r"\bregedit\b", re.IGNORECASE),
    re.compile(r"\b(set-itemproperty|new-itemproperty|remove-itemproperty)\b.*(hklm|hkcu|registry):", re.IGNORECASE),
]

# System power, reboot, and boot modifications
SYSTEM_POWER_PATTERNS: List[Pattern[str]] = [
    re.compile(r"\bshutdown\b", re.IGNORECASE),
    re.compile(r"\brestart-computer\b", re.IGNORECASE),
    re.compile(r"\bstop-computer\b", re.IGNORECASE),
]

# Privilege escalation and security descriptor tampering
SECURITY_TAMPERING_PATTERNS: List[Pattern[str]] = [
    re.compile(r"\btakeown\b", re.IGNORECASE),
    re.compile(r"\bicacls\b.*(\/grant|\/deny|\/remove|\/reset)", re.IGNORECASE),
    re.compile(r"\brunas\b", re.IGNORECASE),
    re.compile(r"\bvssadmin\s+delete\s+shadows\b", re.IGNORECASE),
]

# Credential theft, memory dumping, and sensitive hive extraction
CREDENTIAL_EXTRACTION_PATTERNS: List[Pattern[str]] = [
    re.compile(r"\bmimikatz\b", re.IGNORECASE),
    re.compile(r"\bprocdump\b.*lsass", re.IGNORECASE),
    re.compile(r"\bcomsvcs\.dll.*minidump", re.IGNORECASE),
    re.compile(r"\bget-credential\b", re.IGNORECASE),
    re.compile(r"\bntdsutil\b", re.IGNORECASE),
]

# Obfuscated, encoded, or indirect script execution
OBFUSCATION_PATTERNS: List[Pattern[str]] = [
    re.compile(r"powershell(\.exe)?\s+.*(-enc|-encodedcommand)\b", re.IGNORECASE),
    re.compile(r"\bfrombase64string\b", re.IGNORECASE),
    re.compile(r"\b\[convert\]::\b", re.IGNORECASE),
    re.compile(r"\b(iex|invoke-expression)\b", re.IGNORECASE),
    re.compile(r"\bmshta(\.exe)?\b", re.IGNORECASE),
    re.compile(r"\brundll32(\.exe)?\b", re.IGNORECASE),
    re.compile(r"\bcscript(\.exe)?\b", re.IGNORECASE),
    re.compile(r"\bwscript(\.exe)?\b", re.IGNORECASE),
    re.compile(r"\bcertutil(\.exe)?\s+.*-(urlcache|decode|encode)\b", re.IGNORECASE),
    re.compile(r"\bbitsadmin(\.exe)?\b", re.IGNORECASE),
]

# Network exfiltration, reverse shells, and remote code downloading
NETWORK_EXFILTRATION_PATTERNS: List[Pattern[str]] = [
    re.compile(r"\b(invoke-webrequest|iwr|curl|wget)\b.*\|\s*(iex|invoke-expression)", re.IGNORECASE),
    re.compile(r"\b(nc|ncat|netcat)\b.*-l", re.IGNORECASE),
]

ALL_HARD_DENY_PATTERNS: List[Tuple[str, List[Pattern[str]]]] = [
    ("Storage Destruction", STORAGE_DESTRUCTIVE_PATTERNS),
    ("System Root Destruction", SYSTEM_ROOT_DESTRUCTION_PATTERNS),
    ("Registry Manipulation", REGISTRY_MANIPULATION_PATTERNS),
    ("System Power Modification", SYSTEM_POWER_PATTERNS),
    ("Security & Permission Tampering", SECURITY_TAMPERING_PATTERNS),
    ("Credential Theft & Extraction", CREDENTIAL_EXTRACTION_PATTERNS),
    ("Obfuscation & Indirect Execution", OBFUSCATION_PATTERNS),
    ("Network Exfiltration / Remote Execution", NETWORK_EXFILTRATION_PATTERNS),
]


# ==============================================================================
# Ask-Policy Patterns (Recognized Development and Testing Commands)
# ==============================================================================

SAFE_DEV_COMMAND_PATTERNS: List[Pattern[str]] = [
    # Python & Test Runners
    re.compile(r"^(\.venv[\\/](scripts[\\/])?)?pytest(\.exe)?(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^(\.venv[\\/](scripts[\\/])?)?python(\.exe)?\s+(-m\s+pytest|.*\.py)(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^(\.venv[\\/](scripts[\\/])?)?python(\.exe)?\s+.*$", re.IGNORECASE),
    re.compile(r"^(\.venv[\\/](scripts[\\/])?)?(ruff|black|flake8|mypy)(\.exe)?(\s+.*)?$", re.IGNORECASE),
    # Node.js & Frontend Tools
    re.compile(r"^npm(\.cmd)?\s+(test|run\s+[a-zA-Z0-9_-]+|install|ci|build|lint)(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^npx(\.cmd)?\s+(vitest|vite|tsc|oxlint|eslint|prettier)(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^vitest(\.cmd)?(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^vite(\.cmd)?(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^tsc(\.cmd)?(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^oxlint(\.cmd)?(\s+.*)?$", re.IGNORECASE),
    # Git Inspection (Safe read-only operations)
    re.compile(r"^git(\.exe)?\s+(status|diff|log|branch|show|rev-parse)(\s+.*)?$", re.IGNORECASE),
    # Safe Directory & File Inspection
    re.compile(r"^(dir|ls|get-childitem)(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^(echo|write-output)(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^(cat|type|get-content)(\s+.*)?$", re.IGNORECASE),
    # Other Common Dev Ecosystems
    re.compile(r"^cargo\s+(test|build|check|clippy)(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^go\s+(test|build|vet)(\s+.*)?$", re.IGNORECASE),
    re.compile(r"^dotnet\s+(test|build)(\s+.*)?$", re.IGNORECASE),
]


class CommandPermissionEvaluator:
    """Evaluates commands and tool requests according to the deny-first principle (DENY > ASK > ALLOW)."""

    def __init__(
        self,
        custom_hard_deny_patterns: Optional[List[Pattern[str]]] = None,
        custom_ask_patterns: Optional[List[Pattern[str]]] = None,
        allowed_categories: Optional[Set[ToolRiskCategory]] = None,
    ) -> None:
        """Initialize permission evaluator with optional custom pattern overrides."""
        self.custom_hard_deny_patterns = custom_hard_deny_patterns or []
        self.custom_ask_patterns = custom_ask_patterns or []
        self.allowed_categories = allowed_categories or {
            ToolRiskCategory.READ_ONLY,
            ToolRiskCategory.SYSTEM_PROBE,
            ToolRiskCategory.WORKSPACE_WRITE,
        }

    def evaluate_command(self, command: str, working_dir: Optional[str] = None) -> Tuple[PermissionDecision, str]:
        """Classify a shell command string into DENY, ASK, or fail-closed DENY.

        Precedence:
        1. Hard-deny rules take absolute precedence (cannot be overridden by approval).
        2. Recognized safe development commands require human authorization (ASK).
        3. All other commands fail closed (DENY).
        """
        trimmed = command.strip()
        if not trimmed:
            return PermissionDecision.DENY, "Empty or whitespace command string is prohibited."

        # 1. Evaluate Hard-Deny Rules across all categories
        for category_name, patterns in ALL_HARD_DENY_PATTERNS:
            for pattern in patterns:
                if pattern.search(trimmed):
                    logger.warning("Command '%s' matched hard-deny category: %s", trimmed, category_name)
                    return PermissionDecision.DENY, f"Command blocked by hard-deny policy ({category_name})."

        for custom_pattern in self.custom_hard_deny_patterns:
            if custom_pattern.search(trimmed):
                logger.warning("Command '%s' matched custom hard-deny pattern", trimmed)
                return PermissionDecision.DENY, "Command blocked by custom hard-deny policy."

        # 2. Check for Dangerous Command Chaining / Multi-Command Invocations
        # If the command contains delimiters (;, &&, ||, |), verify all individual segments
        segments = [s.strip() for s in re.split(r"[;&|\n]+", trimmed) if s.strip()]
        if len(segments) > 1:
            for seg in segments:
                for category_name, patterns in ALL_HARD_DENY_PATTERNS:
                    for pattern in patterns:
                        if pattern.search(seg):
                            logger.warning("Chained segment '%s' matched hard-deny category: %s", seg, category_name)
                            return PermissionDecision.DENY, f"Chained command segment blocked ({category_name})."

        # 3. Evaluate Safe Dev Command Patterns (Eligible for Human Approval Gate)
        for pattern in SAFE_DEV_COMMAND_PATTERNS:
            if pattern.match(trimmed):
                return PermissionDecision.ASK, "Development and test execution requires explicit human approval."

        for custom_ask in self.custom_ask_patterns:
            if custom_ask.match(trimmed):
                return PermissionDecision.ASK, "Command requires explicit human approval."

        # 4. Fail Closed for any unclassified command
        logger.warning("Command '%s' was not classified by policy; failing closed to DENY", trimmed)
        return PermissionDecision.DENY, "Command is not recognized as an approved development command by policy."

    async def evaluate_permission(
        self,
        tool: ToolDefinition,
        arguments: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[PermissionDecision, str]:
        """Evaluate authorization for any tool invocation across all risk categories."""
        # Hard check: DESTRUCTIVE category is never auto-allowed
        if tool.risk_category == ToolRiskCategory.DESTRUCTIVE:
            return PermissionDecision.DENY, "Destructive tools are prohibited by default policy."

        # Command Execution requires granular shell command evaluation
        if tool.risk_category == ToolRiskCategory.COMMAND_EXECUTION:
            cmd = str(arguments.get("command", ""))
            cwd = arguments.get("working_dir")
            return self.evaluate_command(cmd, cwd)

        # Contained Workspace Read/Write and System Probes
        if tool.risk_category in self.allowed_categories:
            return PermissionDecision.ALLOW, f"Tool '{tool.name}' authorized by category policy."

        # Unclassified categories fail closed
        return (
            PermissionDecision.DENY,
            f"Tool '{tool.name}' with risk category '{tool.risk_category.value}' denied by policy.",
        )
