"""Tests for Phase 8 CLI Diagnostics and Secret-Free Status Reporting."""

from pathlib import Path
import pytest
from pydantic import SecretStr

from core.config import Settings
from core.diagnostics import (
    check_database_integrity,
    format_report_text,
    mask_secret,
    probe_model_reachability,
    run_diagnostics,
)
from storage.database import DatabaseManager
from storage.migrations import MigrationRunner


@pytest.fixture
def temp_db(tmp_path: Path) -> DatabaseManager:
    """Provide a fresh on-disk SQLite DatabaseManager instance with migrations applied."""
    db_file = tmp_path / "test_diag.db"
    db_url = f"sqlite:///{db_file.as_posix()}"
    manager = DatabaseManager(database_url=db_url)
    runner = MigrationRunner(manager)
    runner.run_migrations()
    yield manager
    manager.close()


def test_mask_secret_invariants() -> None:
    """Verify that secret masking prevents credential leakage under all input formats."""
    # None or empty
    unconf = mask_secret(None)
    assert unconf["configured"] is False
    assert unconf["masked"] is None

    # Short string
    short_res = mask_secret("short")
    assert short_res["configured"] is True
    assert short_res["masked"] == "****"

    # Standard API key
    raw_key = "sk-proj-9876543210abcdef0123456789"
    masked = mask_secret(raw_key)
    assert masked["configured"] is True
    assert masked["masked"].startswith("sk-")
    assert masked["masked"].endswith("789")
    assert "9876543210abcdef" not in masked["masked"]
    assert masked["length"] == len(raw_key)


def test_check_database_integrity(temp_db: DatabaseManager) -> None:
    """Verify that database health inspects integrity, journal mode, and migration version."""
    health = check_database_integrity(db_manager=temp_db)
    assert health["integrity"] == "ok"
    assert health["latest_migration"] >= 3
    assert health["total_migrations"] >= 3


@pytest.mark.asyncio
async def test_probe_model_reachability_unconfigured() -> None:
    """Verify that an unconfigured or empty model endpoint reports unreachable safely."""
    res = await probe_model_reachability(None, None, timeout_seconds=1.0)
    assert res["reachable"] is False
    assert "not set" in res["error"].lower()


@pytest.mark.asyncio
async def test_run_diagnostics_full(temp_db: DatabaseManager) -> None:
    """Verify that run_diagnostics aggregates all subsystem health without exposing secrets."""
    settings = Settings(
        agent_model_api_key=SecretStr("sk-test-secret-key-12345678"),
        database_url=temp_db.db_path,
    )

    diag = await run_diagnostics(settings=settings, db_manager=temp_db)
    assert "configuration" in diag
    assert "model_endpoint" in diag
    assert "database" in diag
    assert "timestamp" in diag

    # Invariant: Secret must never appear in raw form
    assert "sk-test-secret-key-12345678" not in str(diag)

    # Formatter output
    formatted = format_report_text(diag)
    assert "DIAGNOSTIC REPORT" in formatted
    assert "sk-test-secret-key-12345678" not in formatted
    assert "SQLite Database" in formatted
