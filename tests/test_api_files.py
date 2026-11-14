"""Test suite for workspace file inspection and git diff API endpoints."""

import os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from core.config import Settings


@pytest.fixture
def client() -> TestClient:
    """Instantiate test client for application endpoints."""
    settings = Settings(environment="test", log_level="WARNING")
    app = create_app(settings)
    return TestClient(app)


def test_get_file_content_valid_markdown(client: TestClient) -> None:
    """Fetching an existing markdown file returns valid structured content."""
    # Test reading phase-4-test-evidence.md or README.md
    response = client.get("/api/files/content?path=README.md")
    if response.status_code == 404:
        # Fallback to AGENTS.md
        response = client.get("/api/files/content?path=AGENTS.md")

    assert response.status_code == 200
    data = response.json()
    assert "content" in data
    assert data["is_markdown"] is True
    assert data["language"] == "markdown"
    assert data["size_bytes"] > 0


def test_get_file_content_outside_workspace_denied(client: TestClient) -> None:
    """Path traversal attempt outside workspace root is blocked with 403 or 404."""
    response = client.get("/api/files/content?path=../../windows/system32/cmd.exe")
    assert response.status_code in (403, 404)


def test_get_file_content_nonexistent(client: TestClient) -> None:
    """Non-existent file returns 404."""
    response = client.get("/api/files/content?path=docs/reports/non_existent_file_9999.md")
    assert response.status_code == 404


def test_get_git_diff(client: TestClient) -> None:
    """Fetching git diff returns structured diff response."""
    response = client.get("/api/files/diff")
    assert response.status_code == 200
    data = response.json()
    assert "files_changed_count" in data
    assert "insertions" in data
    assert "deletions" in data
    assert "files" in data
    assert isinstance(data["files"], list)


def test_list_available_documents(client: TestClient) -> None:
    """Listing documents returns list of workspace files."""
    response = client.get("/api/files/list")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    # Check shape of items
    first = data[0]
    assert "path" in first
    assert "name" in first
    assert "is_markdown" in first
