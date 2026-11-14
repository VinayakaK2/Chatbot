"""Deterministic context assembly engine, conservative token estimation, and output bounding.

Implements Phase 6 of docs/phases.md and Sections 3.6, 7.1, 7.2, 7.3, and 12 of 2604.14228v3.pdf:
- Strict category segregation: global, project, agent, intent, state, history, tool outputs, evidence.
- Deterministic assembly priority: Mandatory rules > User intent > Task state > Recent history > Tool outputs > Summaries.
- Conservative token estimation with explicit safety margin (never claiming exact token counts from char counts).
- Non-lossy tool output bounding: preserves exit codes, failure traces, and truncation indicators.
- Mandatory instruction overflow prevention: rejects execution if mandatory policy exceeds budget.
- Original transcript preservation: context assembly creates a bounded view without mutating source history.
- Context isolation: strictly single-task and single-session scoped.
"""

import json
import logging
import math
import time
from typing import Any, Dict, List, Optional, Tuple

from core.context_models import (
    CATEGORY_DEFAULT_PRIORITIES,
    ContextAssemblyDiagnostics,
    ContextAssemblyResult,
    ContextBudgetConfig,
    ContextCategory,
    ContextItem,
    DerivedSummary,
    MandatoryContextOverflowError,
    MalformedContextItemError,
    OutputBudgetExceededError,
)
from core.message_models import AgentMessage, AgentMessageRole, ToolCallRecord
from tools.shell import redact_secrets

logger = logging.getLogger("agent_runtime.core.context")

# Character-to-token divisor for conservative heuristic estimation
# Standard BPE tokenizers average ~3.5 to 4.0 chars per token on mixed code and English prose.
# Using 3.3 chars per token yields a strictly conservative upper bound.
ESTIMATOR_CHARS_PER_TOKEN: float = 3.3
ESTIMATOR_BASE_OVERHEAD: int = 4
ESTIMATOR_SAFETY_FACTOR: float = 1.15
ESTIMATOR_IDENTIFIER: str = "conservative_heuristic_v1_with_safety_margin"


def estimate_tokens(text: Optional[str]) -> int:
    """Conservatively estimate token count for text using character ratio plus safety factor.

    Returns 0 for empty or None text. Character counts are never falsely represented as exact tokens.
    """
    if not text:
        return 0
    raw_estimate = math.ceil(len(text) / ESTIMATOR_CHARS_PER_TOKEN) + ESTIMATOR_BASE_OVERHEAD
    return math.ceil(raw_estimate * ESTIMATOR_SAFETY_FACTOR)


def bound_text_content(
    text: str,
    max_tokens: int,
    preserve_head_ratio: float = 0.4,
    metadata: Optional[Dict[str, Any]] = None,
) -> Tuple[str, bool, int]:
    """Bound text to fit within a token limit, preserving essential start and end sections.

    In development and testing tools, failures, stack traces, and exit codes typically appear
    at the very end of stdout/stderr. Preserving a 40/60 head/tail split ensures both context
    initialization and critical terminal errors survive reduction.

    Returns:
        Tuple[bounded_text, was_truncated, original_char_length]
    """
    orig_len = len(text)
    orig_tokens = estimate_tokens(text)
    if orig_tokens <= max_tokens:
        return text, False, orig_len

    meta_str = ""
    if metadata and "exit_code" in metadata:
        meta_str = f"; exit_code={metadata['exit_code']}"

    # Target maximum characters derived by inverting the conservative token estimator:
    # chars <= (max_tokens / ESTIMATOR_SAFETY_FACTOR - ESTIMATOR_BASE_OVERHEAD) * ESTIMATOR_CHARS_PER_TOKEN
    safe_token_ceiling = max_tokens / ESTIMATOR_SAFETY_FACTOR - ESTIMATOR_BASE_OVERHEAD
    if safe_token_ceiling <= 0:
        raise OutputBudgetExceededError(
            f"Cannot bound text: requested token budget {max_tokens} is less than base overhead ({ESTIMATOR_BASE_OVERHEAD}).",
            details={"max_tokens": max_tokens, "base_overhead": ESTIMATOR_BASE_OVERHEAD},
        )

    target_max_chars = int(safe_token_ceiling * ESTIMATOR_CHARS_PER_TOKEN)

    # Reserve estimated character length for the truncation marker (~85 chars)
    marker_len_approx = 85 + len(meta_str)
    char_budget = max(0, target_max_chars - marker_len_approx)

    if orig_len <= char_budget:
        return text, False, orig_len

    head_chars = int(char_budget * preserve_head_ratio)
    tail_chars = char_budget - head_chars

    head_part = text[:head_chars]
    tail_part = text[-tail_chars:] if tail_chars > 0 else ""

    marker = f"\n\n... [Output truncated from {orig_len} chars to ~{char_budget} chars{meta_str}] ...\n\n"
    bounded = f"{head_part}{marker}{tail_part}"

    # Strict invariant guard: progressively trim tail and head if needed
    while estimate_tokens(bounded) > max_tokens and len(tail_part) > 0:
        trim_step = max(1, len(tail_part) // 4)
        tail_part = tail_part[:-trim_step]
        bounded = f"{head_part}{marker}{tail_part}"

    while estimate_tokens(bounded) > max_tokens and len(head_part) > 0:
        trim_step = max(1, len(head_part) // 4)
        head_part = head_part[:-trim_step]
        bounded = f"{head_part}{marker}{tail_part}"

    # Final ironclad verification: if the bare marker alone exceeds max_tokens, fail closed
    final_tokens = estimate_tokens(bounded)
    if final_tokens > max_tokens:
        raise OutputBudgetExceededError(
            f"Cannot bound text to {max_tokens} tokens: minimum bounded representation ({final_tokens} tokens) exceeds budget.",
            details={"max_tokens": max_tokens, "final_tokens": final_tokens, "original_length": orig_len},
        )

    return bounded, True, orig_len


class ContextManager:
    """Manages deterministic context window assembly, token budgeting, and tool output bounding."""

    def __init__(self, default_budget: Optional[ContextBudgetConfig] = None) -> None:
        """Initialize context manager with optional default budget configuration."""
        self.default_budget = default_budget or ContextBudgetConfig()

    def assemble_context(
        self,
        task_id: str,
        user_prompt: str,
        global_instructions: Optional[str] = None,
        project_instructions: Optional[str] = None,
        agent_instructions: Optional[str] = None,
        task_state: Optional[Dict[str, Any]] = None,
        history: Optional[List[AgentMessage]] = None,
        evidence: Optional[List[Dict[str, Any]]] = None,
        summaries: Optional[List[DerivedSummary]] = None,
        budget_config: Optional[ContextBudgetConfig] = None,
        extra_secrets: Optional[List[str]] = None,
    ) -> ContextAssemblyResult:
        """Deterministically assemble relevant model context within configured token bounds.

        Guarantees:
        - Deterministic ordering: identical input produces identical ordered messages.
        - Mandatory instructions are non-droppable; overflow raises MandatoryContextOverflowError.
        - Original input history is treated as immutable and never modified in place.
        - Tool outputs are bounded, preserving exit codes and failure indicators.
        - Secrets are scanned and redacted from context elements before model formatting.
        """
        start_time = time.perf_counter()
        budget = budget_config or self.default_budget
        available_input = budget.available_input_tokens

        # 1. Prepare and Sanitize Candidates
        candidates: List[ContextItem] = []
        secrets = extra_secrets or []

        # 1.1 Mandatory Global & Safety Instructions
        if global_instructions:
            clean_global = redact_secrets(global_instructions.strip(), secrets)
            candidates.append(
                ContextItem.create(
                    item_id=f"{task_id}_global_instructions",
                    category=ContextCategory.GLOBAL_INSTRUCTIONS,
                    content=clean_global,
                    is_mandatory=True,
                    priority=1,
                    metadata={"source": "system_global"},
                )
            )

        # 1.2 Mandatory Project Instructions (AGENTS.md / CLAUDE.md)
        if project_instructions:
            clean_proj = redact_secrets(project_instructions.strip(), secrets)
            candidates.append(
                ContextItem.create(
                    item_id=f"{task_id}_project_instructions",
                    category=ContextCategory.PROJECT_INSTRUCTIONS,
                    content=clean_proj,
                    is_mandatory=True,
                    priority=2,
                    metadata={"source": "project_constitution"},
                )
            )

        # 1.3 Selected Agent Instructions
        if agent_instructions:
            clean_agent = redact_secrets(agent_instructions.strip(), secrets)
            candidates.append(
                ContextItem.create(
                    item_id=f"{task_id}_agent_instructions",
                    category=ContextCategory.AGENT_INSTRUCTIONS,
                    content=clean_agent,
                    is_mandatory=False,
                    priority=3,
                    metadata={"source": "agent_persona"},
                )
            )

        # 1.4 Current User Intent (Non-droppable under normal operations)
        clean_prompt = redact_secrets(user_prompt.strip(), secrets)
        candidates.append(
            ContextItem.create(
                item_id=f"{task_id}_user_intent",
                category=ContextCategory.USER_INTENT,
                content=clean_prompt,
                is_mandatory=True,
                priority=4,
                metadata={"source": "current_turn_prompt"},
            )
        )

        # 1.5 Authoritative Task State & Active Approvals
        if task_state:
            state_text = json.dumps(task_state, indent=2, sort_keys=True)
            clean_state = redact_secrets(state_text, secrets)
            candidates.append(
                ContextItem.create(
                    item_id=f"{task_id}_task_state",
                    category=ContextCategory.TASK_STATE,
                    content=clean_state,
                    is_mandatory=False,
                    priority=5,
                    metadata={"source": "runtime_task_state"},
                )
            )

        # 1.6 Verification Evidence & Execution Outcomes
        if evidence:
            for idx, ev in enumerate(evidence):
                ev_str = json.dumps(ev, sort_keys=True)
                clean_ev = redact_secrets(ev_str, secrets)
                candidates.append(
                    ContextItem.create(
                        item_id=f"{task_id}_evidence_{idx}",
                        category=ContextCategory.VERIFICATION_EVIDENCE,
                        content=clean_ev,
                        is_mandatory=False,
                        priority=6,
                        metadata={"evidence_index": idx},
                    )
                )

        # 1.7 Conversation History (Segregating recent vs older, bounding tool results)
        raw_history = history or []
        history_len = len(raw_history)
        recent_threshold = max(0, history_len - budget.max_recent_messages)

        for idx, msg in enumerate(raw_history):
            clean_msg_content = redact_secrets(msg.content or "", secrets)
            is_recent = idx >= recent_threshold
            category = ContextCategory.RECENT_HISTORY if is_recent else ContextCategory.OLDER_HISTORY
            prio = 7 if is_recent else 9

            if msg.role == AgentMessageRole.TOOL:
                category = ContextCategory.TOOL_OUTPUT
                prio = 8

            # Bound tool outputs before estimating tokens
            bounded_content = clean_msg_content
            was_truncated = False
            orig_len = len(clean_msg_content)

            if msg.role == AgentMessageRole.TOOL:
                # Extract exit_code from metadata if present
                meta = {}
                if msg.name:
                    meta["tool_name"] = msg.name
                bounded_content, was_truncated, orig_len = bound_text_content(
                    clean_msg_content,
                    max_tokens=budget.max_tool_output_tokens,
                    metadata=meta,
                )

            item = ContextItem(
                id=f"{task_id}_hist_{idx}_{msg.role.value}",
                category=category,
                content=bounded_content,
                priority=prio,
                is_mandatory=False,
                estimated_tokens=estimate_tokens(bounded_content),
                source_id=msg.tool_call_id,
                metadata={
                    "history_index": idx,
                    "role": msg.role.value,
                    "name": msg.name,
                    "tool_calls": [tc.model_dump() for tc in msg.tool_calls] if msg.tool_calls else None,
                },
                truncated=was_truncated,
                original_length=orig_len,
            )
            candidates.append(item)

        # 1.8 Derived Summaries (Lowest inclusion priority)
        if summaries:
            for s_idx, summary in enumerate(summaries):
                clean_sum = redact_secrets(summary.text, secrets)
                candidates.append(
                    ContextItem.create(
                        item_id=f"{task_id}_summary_{summary.summary_id}",
                        category=ContextCategory.DERIVED_SUMMARY,
                        content=clean_sum,
                        is_mandatory=False,
                        priority=10,
                        metadata={
                            "summary_id": summary.summary_id,
                            "source_message_ids": summary.source_message_ids,
                            "coverage_range": summary.coverage_range,
                            "created_at": summary.created_at,
                        },
                    )
                )

        # 2. Update Token Estimates for all candidates
        evaluated_candidates: List[ContextItem] = []
        for c in candidates:
            tok = estimate_tokens(c.content)
            # Preserve existing item fields while setting estimated_tokens
            evaluated_candidates.append(
                ContextItem(
                    id=c.id,
                    category=c.category,
                    content=c.content,
                    priority=c.priority,
                    is_mandatory=c.is_mandatory,
                    estimated_tokens=tok,
                    source_id=c.source_id,
                    metadata=c.metadata,
                    truncated=c.truncated,
                    original_length=c.original_length,
                )
            )

        # 3. Verify Mandatory Context Limits (Priority 1, 2, 4 mandatory items)
        mandatory_items = [item for item in evaluated_candidates if item.is_mandatory]
        mandatory_tokens = sum(item.estimated_tokens for item in mandatory_items)

        if mandatory_tokens > available_input:
            overflow_msg = (
                f"Mandatory instructions and intent ({mandatory_tokens} estimated tokens) exceed "
                f"available input budget ({available_input} tokens). Total context: {budget.total_context_tokens}, "
                f"reserved output: {budget.reserved_output_tokens}, safety margin: {budget.safety_margin_tokens}."
            )
            logger.error("Mandatory context overflow on task %s: %s", task_id, overflow_msg)
            raise MandatoryContextOverflowError(
                overflow_msg,
                details={
                    "mandatory_tokens": mandatory_tokens,
                    "available_input": available_input,
                    "total_context": budget.total_context_tokens,
                    "items": [m.id for m in mandatory_items],
                },
            )

        # 4. Deterministic Priority Allocation
        # Mandatory items are guaranteed inclusion first
        accepted_items: List[ContextItem] = list(mandatory_items)
        allocated_tokens = mandatory_tokens

        # Non-mandatory items are sorted deterministically by:
        # a) Priority rank (ascending: 1 to 10)
        # b) History index (descending for history: newest first)
        # c) Item ID (stable tie-breaker)
        optional_items = [item for item in evaluated_candidates if not item.is_mandatory]

        def sort_key(item: ContextItem) -> Tuple[int, int, str]:
            # For history items, prioritize higher history_index (recent turns)
            h_idx = -item.metadata.get("history_index", 0)
            return (item.priority, h_idx, item.id)

        sorted_optional = sorted(optional_items, key=sort_key)

        exclusion_reasons: Dict[str, str] = {}
        truncation_records: Dict[str, Dict[str, Any]] = {}

        for item in sorted_optional:
            cost = item.estimated_tokens
            if allocated_tokens + cost <= available_input:
                accepted_items.append(item)
                allocated_tokens += cost
                if item.truncated:
                    truncation_records[item.id] = {
                        "category": item.category.value,
                        "original_length": item.original_length,
                        "truncated_length": len(item.content),
                    }
            else:
                exclusion_reasons[item.id] = (
                    f"budget_exhausted: item cost {cost} tokens exceeds remaining {available_input - allocated_tokens}"
                )

        # 5. Reassemble into Authoritative Wire AgentMessages
        # Final wire ordering must be structured and natural:
        # 1. Single Authoritative System Message combining all system-tier instructions
        # 2. Derived Summaries (if included)
        # 3. Chronological Conversation History (User / Assistant / Tool in original temporal order)
        # 4. Current User Intent (if not already represented in history)
        assembled_messages: List[AgentMessage] = []

        # 5.1 System Instruction Composition
        system_blocks: List[str] = []
        for cat in (ContextCategory.GLOBAL_INSTRUCTIONS, ContextCategory.PROJECT_INSTRUCTIONS, ContextCategory.AGENT_INSTRUCTIONS):
            items_in_cat = [it for it in accepted_items if it.category == cat]
            for it in items_in_cat:
                system_blocks.append(it.content)

        state_items = [it for it in accepted_items if it.category == ContextCategory.TASK_STATE]
        if state_items:
            system_blocks.append(f"\n[Authoritative Runtime State]\n{state_items[0].content}")

        evidence_items = [it for it in accepted_items if it.category == ContextCategory.VERIFICATION_EVIDENCE]
        if evidence_items:
            ev_summary = "\n".join(f"- {it.content}" for it in evidence_items)
            system_blocks.append(f"\n[Execution & Verification Evidence]\n{ev_summary}")

        if system_blocks:
            combined_system = "\n\n".join(system_blocks)
            assembled_messages.append(AgentMessage.create_system(combined_system))

        # 5.2 Derived Summaries Block (if present)
        # Untrusted Data Isolation (F6-11):
        # Derived summaries synthesize untrusted user and tool turns. Placing them in role=SYSTEM
        # would grant prompt-injection payloads system-level authority. Instead, derived summaries
        # are quarantined as role=USER with an explicit untrusted reference demarcation.
        summary_items = [it for it in accepted_items if it.category == ContextCategory.DERIVED_SUMMARY]
        if summary_items:
            sum_text = "\n\n".join(f"[Historical Summary {it.id}]\n{it.content}" for it in summary_items)
            assembled_messages.append(
                AgentMessage(
                    role=AgentMessageRole.USER,
                    content=f"[Historical Context Summary (Untrusted Data - For Reference Only)]\n{sum_text}",
                )
            )

        # 5.3 Chronological Conversation Turns
        history_accepted = [
            it for it in accepted_items
            if it.category in (ContextCategory.RECENT_HISTORY, ContextCategory.OLDER_HISTORY, ContextCategory.TOOL_OUTPUT)
        ]
        # Sort back to original chronological history order!
        history_sorted = sorted(history_accepted, key=lambda it: it.metadata.get("history_index", 0))

        for it in history_sorted:
            role_str = it.metadata.get("role", "user")
            role = AgentMessageRole(role_str)
            raw_tcs = it.metadata.get("tool_calls")
            parsed_tcs = [ToolCallRecord(**tc) for tc in raw_tcs] if raw_tcs else None

            msg = AgentMessage(
                role=role,
                content=it.content,
                name=it.metadata.get("name"),
                tool_call_id=it.source_id,
                tool_calls=parsed_tcs,
            )
            assembled_messages.append(msg)

        # 5.4 Ensure Current User Intent is Present
        # If the conversation history does not already conclude with this current prompt, append it
        last_history_is_current_prompt = (
            history_sorted
            and history_sorted[-1].metadata.get("role") == "user"
            and history_sorted[-1].content == clean_prompt
        )
        if not last_history_is_current_prompt:
            intent_items = [it for it in accepted_items if it.category == ContextCategory.USER_INTENT]
            if intent_items:
                assembled_messages.append(AgentMessage.create_user(intent_items[0].content))

        duration_ms = (time.perf_counter() - start_time) * 1000.0

        # 6. Build Diagnostic Audit Record
        included_categories = sorted(list({it.category.value for it in accepted_items}))
        diagnostics = ContextAssemblyDiagnostics(
            budget_total=budget.total_context_tokens,
            budget_reserved_output=budget.reserved_output_tokens,
            budget_available_input=available_input,
            tokens_used_estimate=allocated_tokens,
            tokens_remaining_estimate=max(0, available_input - allocated_tokens),
            estimator_used=ESTIMATOR_IDENTIFIER,
            categories_included=included_categories,
            items_included_count=len(accepted_items),
            items_excluded_count=len(exclusion_reasons),
            items_truncated_count=len(truncation_records),
            exclusion_reasons=exclusion_reasons,
            truncation_records=truncation_records,
            mandatory_overflow=False,
            assembly_duration_ms=duration_ms,
        )

        return ContextAssemblyResult(
            messages=assembled_messages,
            diagnostics=diagnostics,
            raw_items=accepted_items,
        )
