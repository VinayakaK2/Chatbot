"""Tests for Phase 4 Workspace API endpoints."""

import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.task_service import get_task_service


@pytest.fixture
def client():
    """Create test client for API."""
    app = create_app()
    return TestClient(app)


def test_get_workspace_info(client):
    """Verify GET /api/workspace returns active canonical root, limits, and all 8 tools."""
    resp = client.get("/api/workspace")
    assert resp.status_code == 200
    data = resp.json()

    assert "root_path" in data
    assert "name" in data
    assert data["exists"] is True
    assert "limits" in data
    assert data["limits"]["max_file_size_bytes"] > 0
    assert data["limits"]["max_read_bytes"] > 0

    tools = data["tools"]
    assert len(tools) == 8
    tool_names = [t["name"] for t in tools]
    assert "list_directory" in tool_names
    assert "read_file" in tool_names
    assert "read_file_range" in tool_names
    assert "get_file_metadata" in tool_names
    assert "find_files" in tool_names
    assert "search_files" in tool_names
    assert "write_file" in tool_names
    assert "edit_file" in tool_names


def test_browse_directories(client, tmp_path):
    """Verify GET /api/workspace/directories lists subdirectories."""
    # Create test directories
    sub1 = tmp_path / "sub1"
    sub1.mkdir()
    sub2 = tmp_path / "sub2"
    sub2.mkdir()
    (tmp_path / "file.txt").write_text("hello", encoding="utf-8")

    resp = client.get(f"/api/workspace/directories?base_path={tmp_path}")
    assert resp.status_code == 200
    data = resp.json()

    assert data["current_path"] == str(Path(os.path.realpath(tmp_path)).resolve())
    dir_names = [d["name"] for d in data["directories"]]
    assert "sub1" in dir_names
    assert "sub2" in dir_names
    assert "file.txt" not in dir_names


def test_select_workspace_valid(client, tmp_path):
    """Verify POST /api/workspace/select switches active root."""
    target_dir = tmp_path / "test_project"
    target_dir.mkdir()

    resp = client.post("/api/workspace/select", json={"path": str(target_dir)})
    assert resp.status_code == 200
    data = resp.json()

    expected_path = str(Path(os.path.realpath(target_dir)).resolve())
    assert data["root_path"] == expected_path
    assert data["name"] == "test_project"

    # Reset back to current working directory after test
    client.post("/api/workspace/select", json={"path": str(Path.cwd())})


def test_select_workspace_nonexistent(client):
    """Verify POST /api/workspace/select rejects missing paths."""
    resp = client.post("/api/workspace/select", json={"path": "/nonexistent/random/path/12345"})
    assert resp.status_code == 404
