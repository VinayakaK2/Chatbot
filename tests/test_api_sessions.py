"""Integration tests for Phase 7 Session management API endpoints."""

import pytest
import httpx
from pathlib import Path

from api.app import create_app
from core.config import Settings
from core.task_service import TaskService, set_task_service
from storage.database import DatabaseManager
from storage.models import SessionStatus


@pytest.fixture
def test_task_service(tmp_path: Path) -> TaskService:
    """Create a TaskService backed by an isolated disk database for API testing."""
    db_file = tmp_path / "api_sessions_test.db"
    db_mgr = DatabaseManager(db_path=str(db_file), enable_wal=True)
    settings = Settings(agent_model_base_url=None)
    service = TaskService(settings, database_manager=db_mgr)
    set_task_service(service)
    return service


@pytest.mark.asyncio
class TestSessionEndpoints:
    """Validate session creation, listing, retrieval, and reconstructed context contracts."""

    async def test_create_session_success(self, test_task_service: TaskService) -> None:
        """Verify successful creation of a new session."""
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/sessions",
                json={
                    "title": "Investigate Performance",
                    "workspace_path": "/var/workspace",
                    "metadata": {"tags": ["perf", "p0"]},
                },
            )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data
        assert data["title"] == "Investigate Performance"
        assert data["workspace_path"] == "/var/workspace"
        assert data["status"] == SessionStatus.ACTIVE.value
        assert data["created_at"] is not None

    async def test_create_session_validation_empty_title(self, test_task_service: TaskService) -> None:
        """Verify schema validation rejects empty or missing session title."""
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.post(
                "/api/sessions",
                json={"title": ""},
            )

        assert response.status_code == 422
        data = response.json()
        assert data["error"] == "Validation Error"

    async def test_list_sessions_pagination(self, test_task_service: TaskService) -> None:
        """Verify pagination query parameters (limit and offset) for sessions listing."""
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Create 3 sessions
            for i in range(3):
                await client.post(
                    "/api/sessions",
                    json={"title": f"Session {i}"},
                )

            # Retrieve with limit=2
            res_p1 = await client.get("/api/sessions?limit=2&offset=0")
            assert res_p1.status_code == 200
            items_p1 = res_p1.json()
            assert len(items_p1) == 2

            # Retrieve with offset=2
            res_p2 = await client.get("/api/sessions?limit=2&offset=2")
            assert res_p2.status_code == 200
            items_p2 = res_p2.json()
            assert len(items_p2) >= 1

    async def test_get_session_by_id_success_and_not_found(
        self, test_task_service: TaskService
    ) -> None:
        """Verify session lookup by ID returns 200 when found and 404 when absent."""
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Create session
            create_res = await client.post(
                "/api/sessions",
                json={"title": "Specific Session"},
            )
            session_id = create_res.json()["id"]

            # Lookup by valid ID
            get_res = await client.get(f"/api/sessions/{session_id}")
            assert get_res.status_code == 200
            assert get_res.json()["id"] == session_id

            # Lookup by non-existent ID
            missing_res = await client.get("/api/sessions/non-existent-uuid-999")
            assert missing_res.status_code == 404
            assert "not found" in missing_res.json()["detail"].lower()

    async def test_load_session_context(self, test_task_service: TaskService) -> None:
        """Verify full historical context reconstruction from persistent storage."""
        # 1. Create session directly in service
        session = test_task_service.create_session(
            title="Context Reconstruction Session",
            workspace_path="/workspace",
        )

        # 2. Add tasks and transcript messages
        t1 = test_task_service.task_repo.create_task(
            prompt="First prompt",
            session_id=session.id,
        )
        test_task_service.transcript_repo.append_message(
            session_id=session.id,
            task_id=t1.id,
            role="user",
            content="Hello from operator",
        )
        test_task_service.transcript_repo.append_message(
            session_id=session.id,
            task_id=t1.id,
            role="assistant",
            content="Hello from agent",
        )

        # 3. Call context endpoint
        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            context_res = await client.get(f"/api/sessions/{session.id}/context")
            assert context_res.status_code == 200
            data = context_res.json()
            assert data["session"]["id"] == session.id
            assert len(data["tasks"]) == 1
            assert data["tasks"][0]["id"] == t1.id
            assert len(data["transcript"]) == 2
            assert data["transcript"][0]["role"] == "user"
            assert data["transcript"][1]["role"] == "assistant"

            # Verify 404 for unknown session context
            missing_ctx = await client.get("/api/sessions/unknown-ctx-id/context")
            assert missing_ctx.status_code == 404

    async def test_task_submission_attaches_to_session(
        self, test_task_service: TaskService
    ) -> None:
        """Verify submitting a task with session_id attaches it to the durable session."""
        session = test_task_service.create_session(
            title="Parent Session for Task",
            workspace_path="/workspace",
        )

        app = create_app(Settings())
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            task_res = await client.post(
                "/api/tasks",
                json={
                    "prompt": "Investigate memory usage",
                    "session_id": session.id,
                },
            )

        assert task_res.status_code == 201
        data = task_res.json()
        assert data["session_id"] == session.id
