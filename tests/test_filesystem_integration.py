"""End-to-end integration and adversarial tests for workspace filesystem tools with ToolDispatcher and AgentLoop."""

from pathlib import Path
from typing import Any, Dict, List, Optional
import pytest

from api.schemas.events import RuntimeEvent, RuntimeEventType
from core.agent_loop import AgentExecutionRequest, AgentLoop
from core.message_models import AgentMessageRole, ToolCallRecord, ToolCallStatus
from core.task_service import TaskService
from core.tool_contracts import (
    ToolDefinition,
    ToolResultEnvelope,
    ToolRiskCategory,
)
from core.tool_dispatcher import DefaultPermissionPolicy, ToolDispatcher
from core.tool_registry import ToolRegistry
from core.workspace import WorkspaceContext
from model.contracts import ModelProvider, ModelResponse
from tools.registry_setup import register_workspace_tools


class MockTurnModelProvider(ModelProvider):
    """Deterministic scripted mock model simulating sequential multi-turn tool calling and completion."""

    def __init__(self, responses: List[ModelResponse]) -> None:
        """Initialize with a FIFO queue of planned model responses."""
        self._responses = list(responses)
        self.call_history: List[List[Dict[str, Any]]] = []

    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
        **kwargs: Any,
    ) -> ModelResponse:
        """Pop the next scheduled response and record the conversation history."""
        self.call_history.append(messages)
        if not self._responses:
            return ModelResponse(
                text="Default final fallback answer.",
                finish_reason="stop",
                model="test-mock-model",
            )
        return self._responses.pop(0)

    async def check_connectivity(self) -> bool:
        """Simulate healthy endpoint."""
        return True


@pytest.fixture
def test_workspace(tmp_path: Path) -> WorkspaceContext:
    """Create an isolated, populated test workspace."""
    ws = tmp_path / "agent_ws"
    ws.mkdir(parents=True, exist_ok=True)
    src = ws / "src"
    src.mkdir()
    (src / "app.py").write_text("def app():\n    return 'v1'\n", encoding="utf-8")
    (ws / "data.bin").write_bytes(b"\x00\xFF\x00\xFF")
    return WorkspaceContext(root_path=ws)


@pytest.fixture
def integrated_dispatcher(test_workspace: WorkspaceContext) -> ToolDispatcher:
    """Provide a ToolDispatcher with all 8 Phase 4 workspace tools registered."""
    registry = ToolRegistry()
    register_workspace_tools(registry=registry, workspace=test_workspace)
    return ToolDispatcher(
        registry=registry,
        permission_policy=DefaultPermissionPolicy(),
    )


class TestDispatcherFilesystemIntegration:
    """Verify ToolDispatcher validation, permission, and error normalization for filesystem tools."""

    @pytest.mark.asyncio
    async def test_dispatch_list_directory(self, integrated_dispatcher: ToolDispatcher) -> None:
        """list_directory dispatched through ToolDispatcher returns successful ToolResultEnvelope."""
        result = await integrated_dispatcher.dispatch(
            tool_name="list_directory",
            arguments='{"path": ".", "recursive": false}',
            call_id="call_list_dir_1",
        )
        assert result.success is True
        assert result.call_id == "call_list_dir_1"
        assert result.tool_name == "list_directory"
        assert result.output is not None
        assert isinstance(result.output, dict)
        assert result.output["total_entries"] >= 2
        names = [e["name"] for e in result.output["entries"]]
        assert "src" in names
        assert "data.bin" in names

    @pytest.mark.asyncio
    async def test_dispatch_read_file(self, integrated_dispatcher: ToolDispatcher) -> None:
        """read_file dispatched through ToolDispatcher returns content and metadata."""
        result = await integrated_dispatcher.dispatch(
            tool_name="read_file",
            arguments='{"path": "src/app.py"}',
            call_id="call_read_1",
        )
        assert result.success is True
        assert result.output["path"] == "src/app.py"
        assert "def app():" in result.output["content"]

    @pytest.mark.asyncio
    async def test_dispatch_write_and_edit_file(self, integrated_dispatcher: ToolDispatcher) -> None:
        """write_file and edit_file execute atomically through ToolDispatcher with unified diffs."""
        # Step 1: Write file
        write_res = await integrated_dispatcher.dispatch(
            tool_name="write_file",
            arguments='{"path": "src/config.py", "content": "ENV = \'dev\'\\nDEBUG = True\\n"}',
            call_id="call_write_1",
        )
        assert write_res.success is True
        assert write_res.output["path"] == "src/config.py"
        assert write_res.output["lines_written"] == 2

        # Step 2: Edit file
        edit_res = await integrated_dispatcher.dispatch(
            tool_name="edit_file",
            arguments='{"path": "src/config.py", "old_str": "ENV = \'dev\'", "new_str": "ENV = \'prod\'"}',
            call_id="call_edit_1",
        )
        assert edit_res.success is True
        assert edit_res.output["replacements_made"] == 1
        assert "-ENV = 'dev'" in edit_res.output["diff"]
        assert "+ENV = 'prod'" in edit_res.output["diff"]

        # Step 3: Verify updated file content
        read_res = await integrated_dispatcher.dispatch(
            tool_name="read_file",
            arguments='{"path": "src/config.py"}',
            call_id="call_read_2",
        )
        assert read_res.success is True
        assert "ENV = 'prod'" in read_res.output["content"]

    @pytest.mark.asyncio
    async def test_dispatch_find_and_search_files(self, integrated_dispatcher: ToolDispatcher) -> None:
        """find_files and search_files locate files and line matches through ToolDispatcher."""
        # Find files
        find_res = await integrated_dispatcher.dispatch(
            tool_name="find_files",
            arguments='{"pattern": "*.py"}',
            call_id="call_find_1",
        )
        assert find_res.success is True
        assert "src/app.py" in find_res.output["matches"]

        # Search files
        search_res = await integrated_dispatcher.dispatch(
            tool_name="search_files",
            arguments='{"query": "return \'v1\'"}',
            call_id="call_search_1",
        )
        assert search_res.success is True
        assert search_res.output["total_matches"] == 1
        match = search_res.output["matches"][0]
        assert match["path"] == "src/app.py"
        assert match["line_number"] == 2


class TestAdversarialDispatcherProbes:
    """Forensic adversarial probes verifying containment, attack deflection, and error normalization."""

    @pytest.mark.asyncio
    async def test_traversal_read_fails_gracefully(self, integrated_dispatcher: ToolDispatcher) -> None:
        """Path traversal escape attempt in read_file returns normalized error envelope."""
        result = await integrated_dispatcher.dispatch(
            tool_name="read_file",
            arguments='{"path": "../../../etc/shadow"}',
            call_id="call_traversal_1",
        )
        assert result.success is False
        assert result.error_category == "execution_error"
        assert "outside workspace" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_traversal_write_fails_gracefully(self, integrated_dispatcher: ToolDispatcher) -> None:
        """Path traversal escape attempt in write_file returns normalized error without touching host."""
        result = await integrated_dispatcher.dispatch(
            tool_name="write_file",
            arguments='{"path": "../../pwn.txt", "content": "hacked"}',
            call_id="call_traversal_write",
        )
        assert result.success is False
        assert result.error_category == "execution_error"
        assert "outside workspace" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_reserved_windows_device_write_fails(self, integrated_dispatcher: ToolDispatcher) -> None:
        """Writing to reserved Windows device names (NUL, CON) is blocked before I/O."""
        result = await integrated_dispatcher.dispatch(
            tool_name="write_file",
            arguments='{"path": "CON.txt", "content": "crash"}',
            call_id="call_device_name",
        )
        assert result.success is False
        assert result.error_category == "execution_error"
        assert "reserved windows device" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_alternate_data_stream_write_fails(self, integrated_dispatcher: ToolDispatcher) -> None:
        """Writing with NTFS Alternate Data Stream syntax (':') is blocked before I/O."""
        result = await integrated_dispatcher.dispatch(
            tool_name="write_file",
            arguments='{"path": "file.txt:evil.exe", "content": "payload"}',
            call_id="call_ads",
        )
        assert result.success is False
        assert result.error_category == "execution_error"
        assert "alternate data streams" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_binary_file_read_fails(self, integrated_dispatcher: ToolDispatcher) -> None:
        """Reading binary file returns normalized binary_file error."""
        result = await integrated_dispatcher.dispatch(
            tool_name="read_file",
            arguments='{"path": "data.bin"}',
            call_id="call_bin_read",
        )
        assert result.success is False
        assert result.error_category == "execution_error"
        assert "binary" in (result.error or "").lower()

    @pytest.mark.asyncio
    async def test_edit_conflict_fails(self, integrated_dispatcher: ToolDispatcher) -> None:
        """Editing with non-existent old_str returns normalized edit_conflict error."""
        result = await integrated_dispatcher.dispatch(
            tool_name="edit_file",
            arguments='{"path": "src/app.py", "old_str": "non_existent_code", "new_str": "replacement"}',
            call_id="call_conflict",
        )
        assert result.success is False
        assert result.error_category == "execution_error"
        assert "old_str was not found" in (result.error or "").lower()


class TestAgentLoopFilesystemEndToEnd:
    """Verify multi-turn reactive loop execution driving filesystem tools end-to-end."""

    @pytest.mark.asyncio
    async def test_multi_turn_inspect_and_modify_loop(self, test_workspace: WorkspaceContext) -> None:
        """AgentLoop drives a 3-turn workflow: list_directory -> edit_file -> final response."""
        registry = ToolRegistry()
        register_workspace_tools(registry=registry, workspace=test_workspace)
        dispatcher = ToolDispatcher(registry=registry, permission_policy=DefaultPermissionPolicy())

        # Scripted 3-turn model responses
        turn1_resp = ModelResponse(
            text="Let me inspect the workspace directory.",
            finish_reason="tool_calls",
            model="qwen-14b",
            tool_calls=[
                {
                    "id": "call_turn1_list",
                    "type": "function",
                    "function": {
                        "name": "list_directory",
                        "arguments": '{"path": "src"}',
                    },
                }
            ],
        )

        turn2_resp = ModelResponse(
            text="Now let me update the version in app.py.",
            finish_reason="tool_calls",
            model="qwen-14b",
            tool_calls=[
                {
                    "id": "call_turn2_edit",
                    "type": "function",
                    "function": {
                        "name": "edit_file",
                        "arguments": '{"path": "src/app.py", "old_str": "return \'v1\'", "new_str": "return \'v2\'"}',
                    },
                }
            ],
        )

        turn3_resp = ModelResponse(
            text="I have inspected the project and successfully updated app.py from v1 to v2.",
            finish_reason="stop",
            model="qwen-14b",
            tool_calls=None,
        )

        mock_provider = MockTurnModelProvider([turn1_resp, turn2_resp, turn3_resp])
        agent_loop = AgentLoop(
            model_provider=mock_provider,
            dispatcher=dispatcher,
            registry=registry,
        )

        request = AgentExecutionRequest(
            task_id="task_e2e_fs_1",
            prompt="Inspect src and bump version to v2 in app.py",
        )

        result = await agent_loop.run(request)

        # Invariant Assertions
        assert result.status.value == "completed"
        assert "successfully updated app.py from v1 to v2" in (result.final_response or "")
        assert result.iterations == 3
        assert result.tool_calls_count == 2

        # Verify message history contains tool results paired with call IDs
        tool_result_messages = [
            m for m in result.history if m.role == AgentMessageRole.TOOL
        ]
        assert len(tool_result_messages) == 2

        # Verify on-disk mutation
        target_file = test_workspace.root_path / "src" / "app.py"
        assert "return 'v2'" in target_file.read_text(encoding="utf-8")
        assert tool_result_messages[0].tool_call_id == "call_turn1_list"
        assert tool_result_messages[1].tool_call_id == "call_turn2_edit"

    @pytest.mark.asyncio
    async def test_event_payload_sanitization_sec01(self, test_workspace: WorkspaceContext) -> None:
        """SEC-01 Invariant: Progress events redact sensitive payloads while internal history retains full content."""
        registry = ToolRegistry()
        register_workspace_tools(registry=registry, workspace=test_workspace)
        dispatcher = ToolDispatcher(registry=registry)

        secret_payload = "API_KEY=super_secret_token_12345"
        turn1_resp = ModelResponse(
            text="Writing config",
            finish_reason="tool_calls",
            model="test-model",
            tool_calls=[
                {
                    "id": "call_write_sec",
                    "type": "function",
                    "function": {
                        "name": "write_file",
                        "arguments": f'{{"path": "config.env", "content": "{secret_payload}"}}',
                    },
                }
            ],
        )
        turn2_resp = ModelResponse(
            text="Reading config",
            finish_reason="tool_calls",
            model="test-model",
            tool_calls=[
                {
                    "id": "call_read_sec",
                    "type": "function",
                    "function": {
                        "name": "read_file",
                        "arguments": '{"path": "config.env"}',
                    },
                }
            ],
        )
        turn3_resp = ModelResponse(
            text="Done configuring secrets.",
            finish_reason="stop",
            model="test-model",
            tool_calls=None,
        )

        provider = MockTurnModelProvider([turn1_resp, turn2_resp, turn3_resp])
        agent_loop = AgentLoop(model_provider=provider, dispatcher=dispatcher, registry=registry)

        events: List[RuntimeEvent] = []
        req = AgentExecutionRequest(
            task_id="task_sec_events",
            prompt="Configure secrets and verify",
            event_sink=lambda ev: events.append(ev),
        )

        result = await agent_loop.run(req)
        assert result.status.value == "completed"

        # Check TOOL_REQUESTED for write_file
        req_events = [e for e in events if e.event_type == RuntimeEventType.TOOL_REQUESTED]
        assert len(req_events) == 2
        write_req_payload = req_events[0].payload
        assert write_req_payload["tool_name"] == "write_file"
        assert write_req_payload["file_path"] == "config.env"
        assert write_req_payload["risk_category"] == "workspace_write"
        assert write_req_payload["operation"] == "Write config.env"
        # Secret must NOT be in event arguments
        assert secret_payload not in str(write_req_payload["arguments"])
        assert write_req_payload["arguments"]["content"] == f"<redacted: {len(secret_payload.encode('utf-8'))} bytes>"

        # Check TOOL_COMPLETED for read_file
        comp_events = [e for e in events if e.event_type == RuntimeEventType.TOOL_COMPLETED]
        assert len(comp_events) == 2
        read_comp_payload = comp_events[1].payload
        assert read_comp_payload["tool_name"] == "read_file"
        # Secret content must be redacted in event payload
        assert secret_payload not in str(read_comp_payload["output"])
        assert read_comp_payload["output"]["content_redacted"] is True
        assert "<redacted:" in read_comp_payload["output"]["content"]

        # Crucial Invariant: The internal agent conversation history MUST retain the actual unredacted secret!
        read_tool_msgs = [
            m for m in result.history
            if m.role == AgentMessageRole.TOOL and m.tool_call_id == "call_read_sec"
        ]
        assert len(read_tool_msgs) == 1
        assert secret_payload in read_tool_msgs[0].content

    @pytest.mark.asyncio
    async def test_dispatcher_truncation_propagation_arch03(self, integrated_dispatcher: ToolDispatcher) -> None:
        """ARCH-03 Invariant: Dictionary tool outputs with truncated=True propagate to metadata['truncated']."""
        # Read a file with max_bytes=10 on a file that has more than 10 bytes
        result = await integrated_dispatcher.dispatch(
            tool_name="read_file",
            arguments='{"path": "src/app.py", "max_bytes": 10}',
            call_id="call_trunc_prop",
        )
        assert result.success is True
        assert isinstance(result.output, dict)
        assert result.output.get("truncated") is True
        # Envelope metadata must also reflect truncated=True
        assert result.metadata.get("truncated") is True

    def test_task_service_auto_register_control_arch02(self, test_workspace: WorkspaceContext) -> None:
        """ARCH-02 Invariant: auto_register_tools=False preserves caller-supplied registry."""
        custom_registry = ToolRegistry()
        service_no_auto = TaskService(
            workspace_context=test_workspace,
            tool_registry=custom_registry,
            auto_register_tools=False,
        )
        assert custom_registry.has("read_file") is False
        assert len(custom_registry) == 0

        service_auto = TaskService(
            workspace_context=test_workspace,
            tool_registry=custom_registry,
            auto_register_tools=True,
        )
        assert custom_registry.has("read_file") is True
        assert len(custom_registry) == 8
