"""Context management data models, category classifications, and diagnostic contracts.

Implements Phase 6 of docs/phases.md and Section 7 of 2604.14228v3.pdf:
- Explicit separation of instructions, user intent, task state, history, tool outputs, and derived summaries.
- Configurable token budgets and output reserves.
- Traceable summary provenance contracts.
- Granular assembly diagnostics and structured exception taxonomy.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple
from pydantic import BaseModel, ConfigDict, Field, field_validator


class ContextCategory(str, Enum):
    """Categorical classification of context elements enforcing strict data separation."""

    GLOBAL_INSTRUCTIONS = "global_instructions"
    PROJECT_INSTRUCTIONS = "project_instructions"
    AGENT_INSTRUCTIONS = "agent_instructions"
    USER_INTENT = "user_intent"
    TASK_STATE = "task_state"
    RECENT_HISTORY = "recent_history"
    OLDER_HISTORY = "older_history"
    TOOL_OUTPUT = "tool_output"
    VERIFICATION_EVIDENCE = "verification_evidence"
    DERIVED_SUMMARY = "derived_summary"


# Priority ranks: lower numeric value represents higher inclusion priority
CATEGORY_DEFAULT_PRIORITIES: Dict[ContextCategory, int] = {
    ContextCategory.GLOBAL_INSTRUCTIONS: 1,      # Highest: Mandatory system/safety invariants
    ContextCategory.PROJECT_INSTRUCTIONS: 2,     # High: Workspace architecture/constitution rules
    ContextCategory.AGENT_INSTRUCTIONS: 3,       # High: Persona and operational constraints
    ContextCategory.USER_INTENT: 4,              # Critical: Current user prompt and active objectives
    ContextCategory.TASK_STATE: 5,               # Authoritative runtime state (task ID, turn, limits)
    ContextCategory.VERIFICATION_EVIDENCE: 6,    # Test results, exit codes, assertion outputs
    ContextCategory.RECENT_HISTORY: 7,           # Recent conversation turns
    ContextCategory.TOOL_OUTPUT: 8,              # Bounded tool execution payloads
    ContextCategory.OLDER_HISTORY: 9,            # Prior conversational turns
    ContextCategory.DERIVED_SUMMARY: 10,         # Compacted summaries of historical context
}


class ContextItem(BaseModel):
    """A discrete unit of context considered by the deterministic context assembler."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., description="Unique identifier for tracking and diagnostic reporting")
    category: ContextCategory = Field(..., description="Context taxonomy category")
    content: str = Field(..., description="Text payload of the context element")
    priority: int = Field(default=10, description="Inclusion priority rank (1 = highest, 10 = lowest)")
    is_mandatory: bool = Field(default=False, description="Whether this item is non-droppable")
    estimated_tokens: int = Field(default=0, ge=0, description="Conservative estimated token cost")
    source_id: Optional[str] = Field(default=None, description="Original message ID or tool call ID")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Diagnostic and tracking metadata")
    truncated: bool = Field(default=False, description="Whether content was bounded or truncated")
    original_length: Optional[int] = Field(default=None, ge=0, description="Original char count if truncated")

    @classmethod
    def create(
        cls,
        item_id: str,
        category: ContextCategory,
        content: str,
        is_mandatory: bool = False,
        priority: Optional[int] = None,
        source_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> "ContextItem":
        """Factory method applying default category priority if unspecified."""
        prio = priority if priority is not None else CATEGORY_DEFAULT_PRIORITIES.get(category, 10)
        return cls(
            id=item_id,
            category=category,
            content=content,
            priority=prio,
            is_mandatory=is_mandatory,
            estimated_tokens=0,
            source_id=source_id,
            metadata=metadata or {},
            truncated=False,
            original_length=len(content),
        )


class ContextBudgetConfig(BaseModel):
    """Configuration governing model context window limits and token allocations."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    total_context_tokens: int = Field(
        default=32_768,
        ge=512,
        le=1_000_000,
        description="Total context window capacity supported by target model",
    )
    reserved_output_tokens: int = Field(
        default=2_048,
        ge=128,
        le=32_768,
        description="Tokens reserved exclusively for model completion output generation",
    )
    safety_margin_tokens: int = Field(
        default=512,
        ge=0,
        le=8_192,
        description="Token buffer to absorb estimation variance and prevent provider overflow",
    )
    max_tool_output_tokens: int = Field(
        default=1_000,
        ge=64,
        le=16_384,
        description="Upper token bound per individual tool result before truncation",
    )
    max_recent_messages: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Number of recent conversation turns preserved in high priority",
    )

    @field_validator("reserved_output_tokens")
    @classmethod
    def validate_output_reserve(cls, v: int, info: Any) -> int:
        """Ensure output reservation leaves room within the context window."""
        total = info.data.get("total_context_tokens", 32_768)
        if v >= total:
            raise ValueError(f"reserved_output_tokens ({v}) must be strictly less than total_context_tokens ({total})")
        return v

    @property
    def available_input_tokens(self) -> int:
        """Calculate usable token budget available for input messages."""
        usable = self.total_context_tokens - self.reserved_output_tokens - self.safety_margin_tokens
        return max(0, usable)


class DerivedSummary(BaseModel):
    """Metadata and provenance record for a compacted historical context summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary_id: str = Field(..., description="Unique identifier for the summary record")
    text: str = Field(..., description="Derived summary text representing source turns")
    source_message_ids: List[str] = Field(..., description="Identifiers of original transcript messages summarized")
    coverage_range: Tuple[int, int] = Field(..., description="Start and end indices of covered history turns")
    token_estimate: int = Field(default=0, ge=0, description="Estimated token count of the summary")
    created_at: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="ISO 8601 UTC timestamp of summary generation",
    )
    is_derived: bool = Field(default=True, description="Explicit marker indicating this is derived content")
    creator_model: Optional[str] = Field(default=None, description="Model that produced the summary if applicable")


class ContextAssemblyDiagnostics(BaseModel):
    """Detailed observability diagnostics generated during context assembly."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    budget_total: int = Field(..., description="Configured total model context tokens")
    budget_reserved_output: int = Field(..., description="Configured output token reservation")
    budget_available_input: int = Field(..., description="Calculated usable input token budget")
    tokens_used_estimate: int = Field(..., description="Conservative token sum of all included items")
    tokens_remaining_estimate: int = Field(..., description="Estimated unused input tokens remaining")
    estimator_used: str = Field(..., description="Identifier of the token estimation algorithm")
    categories_included: List[str] = Field(..., description="List of category names present in assembly")
    items_included_count: int = Field(..., description="Count of items successfully included in context")
    items_excluded_count: int = Field(..., description="Count of items dropped due to budget constraints")
    items_truncated_count: int = Field(..., description="Count of tool or history items that were truncated")
    exclusion_reasons: Dict[str, str] = Field(
        default_factory=dict,
        description="Mapping of excluded item IDs to rationale (e.g. 'budget_exhausted')",
    )
    truncation_records: Dict[str, Dict[str, Any]] = Field(
        default_factory=dict,
        description="Diagnostic details for truncated items",
    )
    mandatory_overflow: bool = Field(default=False, description="Whether mandatory instructions exceeded budget")
    assembly_duration_ms: float = Field(default=0.0, description="Assembly latency in milliseconds")


class ContextAssemblyResult(BaseModel):
    """Final output of the deterministic context assembly pipeline."""

    model_config = ConfigDict(frozen=False, extra="forbid")

    messages: List[Any] = Field(..., description="Wire-compatible AgentMessage instances for model provider")
    diagnostics: ContextAssemblyDiagnostics = Field(..., description="Detailed assembly diagnostic metrics")
    raw_items: List[ContextItem] = Field(..., description="Ordered list of included ContextItem objects")


# ==============================================================================
# Structured Exception Taxonomy
# ==============================================================================


class ContextError(Exception):
    """Base exception for all context management and assembly errors."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        """Initialize context error with human-readable message and diagnostic details."""
        super().__init__(message)
        self.message = message
        self.details = details or {}


class MandatoryContextOverflowError(ContextError):
    """Raised when non-droppable mandatory instructions exceed the available input token budget."""


class InvalidBudgetConfigurationError(ContextError):
    """Raised when context budget limits are non-positive or geometrically impossible."""


class MalformedContextItemError(ContextError):
    """Raised when an item violates required structure or validation constraints."""


class ContextAssemblyError(ContextError):
    """Raised when deterministic assembly fails due to internal pipeline errors."""
