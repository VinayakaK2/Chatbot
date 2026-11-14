"""Unit and integration tests for PowerShell subprocess runner, process tree termination, and tool dispatch."""

import asyncio
import os
import pytest
from core.approval import ApprovalManager
from core.permissions import CommandPermissionEvaluator
from core.tool_dispatcher import DefaultPermissionPolicy, ToolDispatcher
from core.tool_registry import ToolRegistry
from core.workspace import WorkspaceContext
from tools.shell import (
    PowerShellRunner,
    create_shell_tools,
    redact_secrets,
)


@pytest.fixture
def test_workspace(tmp_path: os.PathLike) -> WorkspaceContext:
    """Fixture providing a temporary clean WorkspaceContext."""
    ws_dir = tmp_path / "test_workspace"
    ws_dir.mkdir(parents=True, exist_ok=True)
    return WorkspaceContext(root_path=str(ws_dir))


@pytest.fixture
def runner(test_workspace: WorkspaceContext) -> PowerShellRunner:
    """Fixture providing a default PowerShellRunner bounded to the test workspace."""
    return PowerShellRunner(
        workspace=test_workspace,
        default_timeout_seconds=10.0,
        max_output_chars=4096,
        extra_secrets=["SECRET_API_TOKEN_9999"],
    )


class TestPowerShellRunnerExecution:
    """Verify execution of PowerShell commands, stdout/stderr capture, and returncode preservation."""

    @pytest.mark.asyncio
    async def test_simple_echo_success(self, runner: PowerShellRunner) -> None:
        """Simple echo command completes with exit code 0 and captured stdout."""
        res = await runner.run("Write-Output 'agent-test-hello'")
        assert res.exit_code == 0
        assert "agent-test-hello" in res.stdout
        assert res.stderr == ""
        assert res.timed_out is False
        assert res.truncated is False

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_preserved(self, runner: PowerShellRunner) -> None:
        """Nonzero return codes are preserved accurately."""
        res = await runner.run("exit 42")
        assert res.exit_code == 42
        assert res.timed_out is False

    @pytest.mark.asyncio
    async def test_stderr_captured(self, runner: PowerShellRunner) -> None:
        """Standard error stream is captured accurately."""
        res = await runner.run("[Console]::Error.WriteLine('critical-sample-error')")
        assert "critical-sample-error" in res.stderr
        assert res.exit_code == 0

    @pytest.mark.asyncio
    async def test_cwd_containment_inside_workspace(
        self, runner: PowerShellRunner, test_workspace: WorkspaceContext
    ) -> None:
        """Subprocess executes in valid relative subfolder inside workspace."""
        sub_dir = os.path.join(test_workspace.root_path, "sub_project")
        os.makedirs(sub_dir, exist_ok=True)

        res = await runner.run("(Get-Location).Path", working_dir="sub_project")
        assert res.exit_code == 0
        assert os.path.realpath(sub_dir).lower() in res.stdout.strip().lower()

    @pytest.mark.asyncio
    async def test_cwd_traversal_outside_workspace_rejected(
        self, runner: PowerShellRunner, test_workspace: WorkspaceContext
    ) -> None:
        """Working directory escapes outside workspace root are rejected."""
        with pytest.raises(ValueError, match="outside the workspace root"):
            await runner.run("dir", working_dir="..")

        with pytest.raises(ValueError, match="outside the workspace root"):
            await runner.run("dir", working_dir=r"C:\Windows")

    @pytest.mark.asyncio
    async def test_cwd_nonexistent_rejected(self, runner: PowerShellRunner) -> None:
        """Non-existent working directory raises ValueError."""
        with pytest.raises(ValueError, match="does not exist"):
            await runner.run("dir", working_dir="nonexistent_folder_123")


class TestPowerShellRunnerSafetyAndBounds:
    """Verify output limits, secret redaction, and process tree termination."""

    @pytest.mark.asyncio
    async def test_output_truncation_when_exceeding_limit(self, test_workspace: WorkspaceContext) -> None:
        """Output exceeding configured max characters is cleanly truncated."""
        bounded_runner = PowerShellRunner(
            workspace=test_workspace,
            default_timeout_seconds=5.0,
            max_output_chars=200,
        )
        res = await bounded_runner.run("1..500 | ForEach-Object { 'line-' + $_ }")
        assert res.truncated is True
        assert "[Output truncated to 200 chars]" in res.stdout
        assert len(res.stdout) <= 300

    def test_redact_secrets_utility(self) -> None:
        """redact_secrets properly redacts configured secrets and bearer tokens."""
        raw = "User token is SECRET_API_TOKEN_9999 and header Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.demo"
        clean = redact_secrets(raw, extra_secrets=["SECRET_API_TOKEN_9999"])
        assert "SECRET_API_TOKEN_9999" not in clean
        assert "eyJhbGciOiJIUzI1NiJ9" not in clean
        assert "[REDACTED_SECRET]" in clean

    @pytest.mark.asyncio
    async def test_runner_redacts_configured_secrets(self, runner: PowerShellRunner) -> None:
        """Subprocess output containing sensitive keys is redacted before return."""
        cmd = "Write-Output 'Config value is SECRET_API_TOKEN_9999'"
        res = await runner.run(cmd)
        assert "SECRET_API_TOKEN_9999" not in res.stdout
        assert "[REDACTED_SECRET]" in res.stdout

    @pytest.mark.asyncio
    async def test_timeout_and_process_tree_termination(self, test_workspace: WorkspaceContext) -> None:
        """Processes exceeding timeout are killed via taskkill and return timed_out=True."""
        short_runner = PowerShellRunner(
            workspace=test_workspace,
            default_timeout_seconds=0.5,
        )
        res = await short_runner.run("Start-Sleep -Seconds 10")
        assert res.timed_out is True
        assert res.exit_code == -1


class TestShellToolIntegrationWithDispatcher:
    """Verify tool registration and end-to-end authorization flow through ToolDispatcher."""

    @pytest.mark.asyncio
    async def test_dispatcher_approved_command_flow(self, test_workspace: WorkspaceContext) -> None:
        """An approval-eligible command executes when approved by human decision authority."""
        registry = ToolRegistry()
        tools = create_shell_tools(workspace=test_workspace)
        for t in tools:
            registry.register(t)

        approval_mgr = ApprovalManager()
        dispatcher = ToolDispatcher(
            registry=registry,
            permission_policy=DefaultPermissionPolicy(evaluator=CommandPermissionEvaluator()),
            approval_manager=approval_mgr,
        )

        async def approve_background():
            await asyncio.sleep(0.05)
            # Find the pending request
            for req in approval_mgr._requests.values():
                if req.status.value == "pending":
                    approval_mgr.resolve_request(req.request_id, approved=True, reason="Test approver")
                    break

        asyncio.create_task(approve_background())

        envelope = await dispatcher.dispatch(
            tool_name="execute_command",
            arguments={"command": "echo dispatch_ok"},
            call_id="call_disp_1",
            context={"task_id": "task_disp_1"},
        )

        assert envelope.success is True
        assert envelope.output["exit_code"] == 0
        assert "dispatch_ok" in envelope.output["stdout"]
        assert envelope.metadata.get("exit_code") == 0

    @pytest.mark.asyncio
    async def test_dispatcher_rejected_command_flow(self, test_workspace: WorkspaceContext) -> None:
        """A command rejected by the human approver returns a permission_denied error envelope."""
        registry = ToolRegistry()
        tools = create_shell_tools(workspace=test_workspace)
        for t in tools:
            registry.register(t)

        approval_mgr = ApprovalManager()
        dispatcher = ToolDispatcher(
            registry=registry,
            permission_policy=DefaultPermissionPolicy(evaluator=CommandPermissionEvaluator()),
            approval_manager=approval_mgr,
        )

        async def reject_background():
            await asyncio.sleep(0.05)
            for req in approval_mgr._requests.values():
                if req.status.value == "pending":
                    approval_mgr.resolve_request(req.request_id, approved=False, reason="Rejecting command")
                    break

        asyncio.create_task(reject_background())

        envelope = await dispatcher.dispatch(
            tool_name="execute_command",
            arguments={"command": "pytest"},
            call_id="call_disp_2",
            context={"task_id": "task_disp_2"},
        )

        assert envelope.success is False
        assert envelope.error_category == "permission_denied"
        assert "Rejecting command" in envelope.error

    @pytest.mark.asyncio
    async def test_dispatcher_hard_denied_command_blocked_immediately(
        self, test_workspace: WorkspaceContext
    ) -> None:
        """A hard-denied command (e.g. format C:) is blocked immediately without creating approval requests."""
        registry = ToolRegistry()
        tools = create_shell_tools(workspace=test_workspace)
        for t in tools:
            registry.register(t)

        approval_mgr = ApprovalManager()
        dispatcher = ToolDispatcher(
            registry=registry,
            permission_policy=DefaultPermissionPolicy(evaluator=CommandPermissionEvaluator()),
            approval_manager=approval_mgr,
        )

        envelope = await dispatcher.dispatch(
            tool_name="execute_command",
            arguments={"command": "format C:"},
            call_id="call_disp_3",
            context={"task_id": "task_disp_3"},
        )

        assert envelope.success is False
        assert envelope.error_category == "permission_denied"
        assert "hard-deny" in envelope.error.lower()
        # Ensure zero approval requests were created
        assert len(approval_mgr._requests) == 0
