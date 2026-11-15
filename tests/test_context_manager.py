"""Comprehensive test suite for Context Management, token budgeting, and output bounding.

Verifies Phase 6 requirements from docs/phases.md and Section 7 of 2604.14228v3.pdf:
- Deterministic context assembly ordering and priority
- Conservative token estimation and safety margin
- Tool output bounding and exit code preservation
- Mandatory instruction overflow prevention
- Secret redaction across all context elements
- Original transcript preservation and immutability
- Derived summary provenance tracking
- Observability and diagnostic reporting
"""

import pytest
from core.context_models import (
    ContextBudgetConfig,
    ContextCategory,
    ContextItem,
    DerivedSummary,
    MandatoryContextOverflowError,
    OutputBudgetExceededError,
)
from core.context_manager import (
    ContextManager,
    bound_text_content,
    estimate_tokens,
    ESTIMATOR_IDENTIFIER,
)
from core.message_models import AgentMessage, AgentMessageRole


class TestTokenEstimator:
    """Verifies conservative token estimation mechanics and safety bounds."""

    def test_empty_and_none_text_returns_zero(self) -> None:
        assert estimate_tokens(None) == 0
        assert estimate_tokens("") == 0

    def test_conservative_estimation_with_safety_margin(self) -> None:
        sample_text = "The quick brown fox jumps over the lazy dog."  # 44 chars
        # 44 / 3.3 = 13.33 -> ceil = 14 + 4 overhead = 18 raw -> 18 * 1.15 = 20.7 -> ceil = 21 tokens
        est = estimate_tokens(sample_text)
        assert est > 0
        assert est >= 20  # strictly conservative upper bound

    def test_large_payload_scales_predictably(self) -> None:
        large_text = "A" * 3300  # ~1000 raw tokens
        est = estimate_tokens(large_text)
        assert est > 1000
        assert est <= 1300

    def test_exact_formula_mathematical_table_verification(self) -> None:
        """Verify the exact mathematical reference values under E(n) = ceil((ceil(n / 3.3) + 4) * 1.15)."""
        expected_table = [
            (129, 51),
            (239, 89),
            (524, 188),
            (1384, 488),
            (2833, 993),
            (5699, 1991),
        ]
        for char_count, expected_tokens in expected_table:
            actual = estimate_tokens("x" * char_count)
            assert actual == expected_tokens, f"Char count {char_count}: expected {expected_tokens}, got {actual}"

    def test_boundary_transitions_and_single_char_steps(self) -> None:
        """Verify boundary values (0, 1, 2, 3, 4) and transition points around 3.3 step intervals."""
        assert estimate_tokens("") == 0
        assert estimate_tokens("a") == 6   # ceil(1/3.3)=1 -> 1+4=5 -> 5*1.15=5.75 -> ceil=6
        assert estimate_tokens("aa") == 6  # ceil(2/3.3)=1 -> 1+4=5 -> ceil=6
        assert estimate_tokens("aaa") == 6 # ceil(3/3.3)=1 -> 1+4=5 -> ceil=6
        assert estimate_tokens("aaaa") == 7 # ceil(4/3.3)=2 -> 2+4=6 -> 6*1.15=6.9 -> ceil=7

    def test_multilingual_unicode_cjk_emoji_estimation(self) -> None:
        """Verify that Unicode, CJK, emoji sequences, and code blocks evaluate predictably."""
        # Unicode Latin / Accented
        latin_text = "Café résumé naïve façade"
        assert estimate_tokens(latin_text) > 0

        # CJK / Japanese text
        cjk_text = "吾輩は猫である。名前はまだ無い。" # 16 characters
        assert estimate_tokens(cjk_text) == estimate_tokens("a" * len(cjk_text))

        # Emoji sequences
        emoji_text = "🚀🔥💻🤖🎉" # 5 characters
        assert estimate_tokens(emoji_text) == estimate_tokens("a" * len(emoji_text))

        # Multiline code block with indentation
        code_block = "def factorial(n: int) -> int:\n    if n <= 1:\n        return 1\n    return n * factorial(n - 1)\n"
        assert estimate_tokens(code_block) > 0
        assert estimate_tokens(code_block) == estimate_tokens("x" * len(code_block))


class TestToolOutputBounding:
    """Verifies non-lossy tool output bounding, head/tail preservation, and exit code metadata."""

    def test_short_output_is_not_truncated(self) -> None:
        short_out = "Ran 5 tests in 0.12s. All passed."
        bounded, truncated, orig_len = bound_text_content(short_out, max_tokens=100)
        assert truncated is False
        assert bounded == short_out
        assert orig_len == len(short_out)

    def test_oversized_output_is_bounded_with_metadata(self) -> None:
        # Construct large test output where failure traceback appears at the bottom
        head_text = "=== TEST RUN START ===\nRunning pytest on tests/test_core.py...\n"
        middle_noise = ".\n" * 5000
        tail_text = "\nFAILED tests/test_core.py::test_failure - AssertionError: expected 42 got 0\n=== 1 failed, 49 passed ==="
        full_output = f"{head_text}{middle_noise}{tail_text}"

        bounded, truncated, orig_len = bound_text_content(
            full_output,
            max_tokens=200,
            metadata={"exit_code": 1},
        )
        assert truncated is True
        assert orig_len == len(full_output)
        assert len(bounded) < len(full_output)
        # Verify critical head text preserved
        assert "TEST RUN START" in bounded
        # Verify critical tail error text preserved
        assert "AssertionError: expected 42 got 0" in bounded
        assert "1 failed, 49 passed" in bounded
        # Verify explicit truncation marker with exit code
        assert "[Output truncated from" in bounded
        assert "exit_code=1" in bounded
        assert estimate_tokens(bounded) <= 200

    def test_bounded_output_never_exceeds_max_tokens_budget(self) -> None:
        """Verify that bounded output estimated tokens strictly never exceeds the requested max_tokens."""
        huge_payload = "def test_metrics():\n    assert True\n" * 2000
        for tok_limit in [64, 100, 200, 500, 1000, 2000]:
            bounded, truncated, _ = bound_text_content(
                huge_payload,
                max_tokens=tok_limit,
                metadata={"exit_code": 0},
            )
            assert truncated is True
            assert estimate_tokens(bounded) <= tok_limit

    def test_impossibly_small_budget_raises_error(self) -> None:
        """Verify that an impossibly small token limit raises OutputBudgetExceededError fail-closed."""
        huge_payload = "data " * 1000
        # 5 tokens is less than base overhead (4 * 1.15 = 4.6), so cannot fit anything
        with pytest.raises(OutputBudgetExceededError) as exc_info:
            bound_text_content(huge_payload, max_tokens=4)
        assert "less than base overhead" in str(exc_info.value)

    def test_boundary_one_unit_below_and_above_cap(self) -> None:
        """Verify boundary behavior with limits right around critical thresholds."""
        payload = "Line of log output with error details.\n" * 500
        for cap in [63, 64, 65, 99, 100, 101, 199, 200, 201]:
            bounded, truncated, _ = bound_text_content(payload, max_tokens=cap)
            assert truncated is True
            assert estimate_tokens(bounded) <= cap, f"Failed cap={cap}: got {estimate_tokens(bounded)}"

    def test_heavy_metadata_and_progressive_trimming(self) -> None:
        """Verify that large metadata strings do not cause bounded output to overshoot cap."""
        payload = "Traceback (most recent call last):\n  File 'test.py', line 42\nValueError: failed\n" * 200
        # Extreme metadata with lengthy command and exit code
        meta = {"exit_code": 137, "command": "pytest --verbose -k test_concurrency_race_condition_under_heavy_load"}
        bounded, truncated, _ = bound_text_content(payload, max_tokens=80, metadata=meta)
        assert truncated is True
        assert "exit_code=137" in bounded
        assert estimate_tokens(bounded) <= 80


class TestDeterministicAssembly:
    """Verifies deterministic context ordering, priority allocation, and history preservation."""

    def test_identical_inputs_produce_identical_assembly(self) -> None:
        mgr = ContextManager()
        hist = [
            AgentMessage.create_user("Initialize repository"),
            AgentMessage.create_assistant("Repository initialized."),
        ]
        res1 = mgr.assemble_context(
            task_id="task_det_1",
            user_prompt="Run project tests",
            global_instructions="Do not leak secrets.",
            project_instructions="Follow PEP 8.",
            history=hist,
        )
        res2 = mgr.assemble_context(
            task_id="task_det_1",
            user_prompt="Run project tests",
            global_instructions="Do not leak secrets.",
            project_instructions="Follow PEP 8.",
            history=hist,
        )

        assert len(res1.messages) == len(res2.messages)
        for m1, m2 in zip(res1.messages, res2.messages):
            assert m1.role == m2.role
            assert m1.content == m2.content

        assert res1.diagnostics.tokens_used_estimate == res2.diagnostics.tokens_used_estimate
        assert res1.diagnostics.categories_included == res2.diagnostics.categories_included

    def test_original_history_is_not_mutated(self) -> None:
        mgr = ContextManager()
        original_msg = AgentMessage.create_user("Original prompt")
        hist = [original_msg]

        res = mgr.assemble_context(
            task_id="task_mut_1",
            user_prompt="Follow-up question",
            history=hist,
        )
        assert len(hist) == 1
        assert hist[0] is original_msg
        assert hist[0].content == "Original prompt"

    def test_chronological_conversation_order_preserved(self) -> None:
        mgr = ContextManager()
        hist = [
            AgentMessage.create_user("Step 1: Check status"),
            AgentMessage.create_assistant("Checking status..."),
            AgentMessage(role=AgentMessageRole.TOOL, content="Status: clean", name="git_status", tool_call_id="call_1"),
            AgentMessage.create_assistant("Status is clean."),
        ]
        res = mgr.assemble_context(
            task_id="task_chrono_1",
            user_prompt="Step 2: Run tests",
            history=hist,
        )

        # Wire messages should start with system instruction, then chronologically ordered turns
        non_system = [m for m in res.messages if m.role != AgentMessageRole.SYSTEM]
        assert len(non_system) >= 4
        assert non_system[0].content == "Step 1: Check status"
        assert non_system[1].content == "Checking status..."
        assert non_system[2].role == AgentMessageRole.TOOL
        assert non_system[3].content == "Status is clean."

    def test_secret_redaction_across_all_categories(self) -> None:
        mgr = ContextManager()
        hist = [
            AgentMessage.create_user("My token is Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.secret123"),
            AgentMessage(role=AgentMessageRole.TOOL, content="Server returned sk-ant-api03-abcdefghijklmnop12345678 and Authorization: Basic dXNlcjpwYXNzd29yZDEyMzQ1Ng==", tool_call_id="call_2"),
        ]
        res = mgr.assemble_context(
            task_id="task_sec_1",
            user_prompt="Audit API key: AKIA1234567890ABCDEF with Authorization: Bearer top_secret_credential_token",
            global_instructions="Safety rule: password is super_secret_pass_123",
            history=hist,
            extra_secrets=["super_secret_pass_123"],
        )

        full_assembled_text = " ".join(m.content or "" for m in res.messages)
        assert "super_secret_pass_123" not in full_assembled_text
        assert "[REDACTED_SECRET]" in full_assembled_text
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in full_assembled_text
        assert "sk-ant-api03-abcdefghijklmnop12345678" not in full_assembled_text
        assert "dXNlcjpwYXNzd29yZDEyMzQ1Ng==" not in full_assembled_text
        assert "top_secret_credential_token" not in full_assembled_text


class TestBudgetAndOverflow:
    """Verifies budget enforcement, optional item exclusion, and mandatory overflow detection."""

    def test_mandatory_instruction_overflow_raises_error(self) -> None:
        mgr = ContextManager()
        # Tight budget: 1000 total tokens - 800 output reserve - 100 safety margin = 100 input tokens
        tight_budget = ContextBudgetConfig(
            total_context_tokens=1000,
            reserved_output_tokens=800,
            safety_margin_tokens=100,
        )
        huge_mandatory = "Mandatory security rule: " * 50  # ~400 tokens

        with pytest.raises(MandatoryContextOverflowError) as exc_info:
            mgr.assemble_context(
                task_id="task_over_1",
                user_prompt="Do something",
                global_instructions=huge_mandatory,
                budget_config=tight_budget,
            )
        assert "exceed available input budget" in str(exc_info.value)
        assert exc_info.value.details["available_input"] == 100

    def test_budget_exhaustion_drops_lower_priority_items(self) -> None:
        mgr = ContextManager()
        # Tight budget: 1000 total - 600 output - 100 margin = 300 input tokens
        budget = ContextBudgetConfig(
            total_context_tokens=1000,
            reserved_output_tokens=600,
            safety_margin_tokens=100,
            max_recent_messages=2,
        )
        # Create history with multiple large older turns that exceed 300 input tokens
        hist = [
            AgentMessage.create_user("Old turn 1 " + "bulk data " * 200),
            AgentMessage.create_assistant("Old response 1 " + "bulk data " * 200),
            AgentMessage.create_user("Recent turn " + "short data " * 5),
            AgentMessage.create_assistant("Recent response " + "short data " * 5),
        ]
        res = mgr.assemble_context(
            task_id="task_drop_1",
            user_prompt="Final task prompt",
            global_instructions="Short system prompt.",
            history=hist,
            budget_config=budget,
        )

        assert res.diagnostics.tokens_used_estimate <= budget.available_input_tokens
        # Verify older turns were excluded due to budget exhaustion
        assert res.diagnostics.items_excluded_count >= 1
        assert any("budget_exhausted" in reason for reason in res.diagnostics.exclusion_reasons.values())


class TestSummaryProvenance:
    """Verifies derived summaries carrying traceable provenance and non-authoritative tagging."""

    def test_derived_summary_includes_provenance_metadata(self) -> None:
        mgr = ContextManager()
        summary = DerivedSummary(
            summary_id="sum_42",
            text="The agent inspected the repository and verified 10 tests passed.",
            source_message_ids=["msg_1", "msg_2", "msg_3"],
            coverage_range=(0, 2),
            token_estimate=25,
        )

        res = mgr.assemble_context(
            task_id="task_sum_1",
            user_prompt="What is the next step?",
            summaries=[summary],
        )

        # Ensure summary message exists and is explicitly quarantined as untrusted user role
        summary_msgs = [m for m in res.messages if "Historical Context Summary" in (m.content or "")]
        assert len(summary_msgs) == 1
        assert summary_msgs[0].role == AgentMessageRole.USER
        assert "sum_42" in summary_msgs[0].content
        assert summary.text in summary_msgs[0].content
        assert res.diagnostics.items_included_count >= 2


class TestToolCallCompaction:
    """Verifies that large file payload arguments in historical assistant tool calls are compacted to prevent context bloat."""

    def test_large_write_file_payload_is_compacted_in_older_history(self) -> None:
        import json
        from core.message_models import ToolCallRecord

        mgr = ContextManager()
        # Budget configured with max_recent_messages=2 so older messages get compacted
        budget = ContextBudgetConfig(max_recent_messages=2)

        huge_css = "/* CSS Rule */\n.btn { color: red; }\n" * 100
        old_assistant = AgentMessage.create_assistant(
            content="Writing styles",
            tool_calls=[
                ToolCallRecord(
                    id="call_write_1",
                    name="write_file",
                    raw_arguments=json.dumps({"path": "css/styles.css", "content": huge_css}),
                )
            ],
        )
        old_tool = AgentMessage(
            role=AgentMessageRole.TOOL,
            content='{"status":"success","bytes_written":3000}',
            name="write_file",
            tool_call_id="call_write_1",
        )
        recent_assistant = AgentMessage.create_assistant(content="Done with styles.")
        recent_user = AgentMessage.create_user("Next step?")

        hist = [old_assistant, old_tool, recent_assistant, recent_user]

        res = mgr.assemble_context(
            task_id="task_compact_1",
            user_prompt="Now build html",
            history=hist,
            budget_config=budget,
        )

        # Find the wire message corresponding to old_assistant
        wire_assistants = [m for m in res.messages if m.role == AgentMessageRole.ASSISTANT and m.tool_calls]
        assert len(wire_assistants) == 1
        tc = wire_assistants[0].tool_calls[0]
        parsed_args = json.loads(tc.raw_arguments)

        # Verify bulk content was compacted into a confirmation indicator preserving path and length
        assert "Content already written to css/styles.css" in parsed_args["content"]
        assert len(parsed_args["content"]) < 200
        assert parsed_args["path"] == "css/styles.css"

        # Verify original message object was NOT mutated
        orig_parsed = json.loads(old_assistant.tool_calls[0].raw_arguments)
        assert orig_parsed["content"] == huge_css

