from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_windows_impactx_launcher_uses_private_generated_environment() -> None:
    launcher = (ROOT / "scripts" / "start_impactx.ps1").read_text(encoding="utf-8")

    assert "ECDAT_DB_PASSWORD" in launcher
    assert "ECDAT_TOKEN_SECRET" in launcher
    assert "ECDAT_USERS_JSON" in launcher
    assert "--env-file" in launcher
    assert '"up"' in launcher, "launcher must start the Compose project"
    assert ".env.*" in (ROOT / ".gitignore").read_text(encoding="utf-8")


def test_dashboard_container_has_impactx_runtime_hardening() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    dashboard = compose.split("  dashboard:", 1)[1].split("\nvolumes:", 1)[0]
    assert "read_only: true" in dashboard
    assert "no-new-privileges:true" in dashboard
    assert "cap_drop:" in dashboard and "- ALL" in dashboard
    assert "/tmp:rw,noexec,nosuid,nodev" in dashboard
