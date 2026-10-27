"""End-to-end integration test validating live TCP socket binding and HTTP responses."""

import threading
import time
import httpx
import pytest
import uvicorn

from api.app import create_app
from core.config import Settings
from core.task_service import TaskService, set_task_service


@pytest.mark.asyncio
async def test_live_server_socket_binding() -> None:
    """Verify uvicorn binds to a live localhost port and serves HTTP requests."""
    # Use a dedicated port for testing to avoid collisions
    test_port = 8765
    settings = Settings(port=test_port)
    set_task_service(TaskService(settings=settings))
    app = create_app(settings)

    config = uvicorn.Config(app, host="127.0.0.1", port=test_port, log_level="error")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base_url = f"http://127.0.0.1:{test_port}"
    server_ready = False

    try:
        # Await live socket binding
        for _ in range(25):
            try:
                async with httpx.AsyncClient(timeout=1.0) as client:
                    resp = await client.get(f"{base_url}/api/health")
                    if resp.status_code == 200:
                        server_ready = True
                        break
            except Exception:
                time.sleep(0.15)

        assert server_ready is True, "Server failed to start and bind within timeout"

        # Verify health response schema
        async with httpx.AsyncClient(timeout=2.0) as client:
            health_resp = await client.get(f"{base_url}/api/health")
            assert health_resp.status_code == 200
            health_json = health_resp.json()
            assert health_json["status"] == "healthy"

            # Verify task submission contract
            task_resp = await client.post(
                f"{base_url}/api/tasks",
                json={"prompt": "Socket smoke test prompt"},
            )
            assert task_resp.status_code == 201
            task_json = task_resp.json()
            assert task_json["status"] == "pending"
            assert "task_id" in task_json

    finally:
        server.should_exit = True
