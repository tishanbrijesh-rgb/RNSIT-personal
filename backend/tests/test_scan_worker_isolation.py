"""Regression tests for the supervised scanner process boundary."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.scan_worker import _json_safe, _publish_progress
from backend.services import scan_control


def test_worker_command_includes_progress_artifact(tmp_path: Path) -> None:
    result_path = tmp_path / "result.json"
    progress_path = tmp_path / "progress.json"

    command = scan_control.worker_command("/repo", result_path, progress_path)

    assert command[-3:] == ["/repo", str(result_path), str(progress_path)]


def test_worker_runtime_uses_configured_writable_storage(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("ECDAT_RUNTIME_DIR", str(tmp_path))

    assert scan_control.runtime_directory() == tmp_path
    assert scan_control._cancel_signal(37) == tmp_path / "scan-37.cancel"


def test_worker_result_normalizes_nested_binary_certificate_values() -> None:
    class CertificateValue:
        def __str__(self) -> str:
            return "certificate-value"

    assert _json_safe({"san": [b"\x01\xff", CertificateValue()]}) == {
        "san": ["01ff", "certificate-value"],
    }


def test_progress_publish_survives_a_windows_destination_lock(tmp_path: Path) -> None:
    progress_path = tmp_path / "progress.json"
    real_replace = os.replace
    attempts = 0

    def locked_once(source: Path, destination: Path) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise PermissionError(5, "destination is temporarily locked")
        real_replace(source, destination)

    with (
        patch("backend.scan_worker.os.replace", side_effect=locked_once),
        patch("backend.scan_worker.time.sleep") as sleep,
    ):
        _publish_progress(progress_path, {"_files_processed": 37})

    assert attempts == 2
    sleep.assert_called_once()
    assert json.loads(progress_path.read_text(encoding="utf-8")) == {
        "_files_processed": 37,
    }


def test_worker_environment_is_allowlisted_and_strips_server_secrets(tmp_path: Path) -> None:
    inherited = {
        "DATABASE_URL": "postgresql://user:password@db/ecdat",
        "ECDAT_TOKEN_SECRET": "signing-secret",
        "ECDAT_USERS_JSON": '{"admin":{"password":"secret"}}',
        "ECDAT_DB_PASSWORD": "database-secret",
        "ECDAT_MAX_FILE_BYTES": "4096",
        "ECDAT_CORRELATOR_VERSION": "v3",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", "C:\\Windows"),
    }

    with patch.dict(os.environ, inherited, clear=True):
        worker_env = scan_control.worker_environment(tmp_path)

    assert worker_env["ECDAT_MAX_FILE_BYTES"] == "4096"
    assert worker_env["ECDAT_CORRELATOR_VERSION"] == "v3"
    assert worker_env["TMPDIR"] == str(tmp_path)
    assert "DATABASE_URL" not in worker_env
    assert "ECDAT_TOKEN_SECRET" not in worker_env
    assert "ECDAT_USERS_JSON" not in worker_env
    assert "ECDAT_DB_PASSWORD" not in worker_env


def test_supervisor_starts_worker_in_a_separate_process_group(tmp_path: Path) -> None:
    process = MagicMock(pid=4321, returncode=1)
    process.poll.return_value = 1
    process.wait.return_value = 1
    control = MagicMock(scan_id=7, timeout=5)
    control.cancel.is_set.return_value = False

    with (
        patch.object(scan_control.tempfile, "TemporaryDirectory") as temporary_directory,
        patch.object(scan_control.subprocess, "Popen", return_value=process) as popen,
        patch.object(scan_control, "_finish_if_active"),
        patch.object(scan_control, "release"),
    ):
        temporary_directory.return_value.__enter__.return_value = str(tmp_path)
        scan_control.supervise("/repo", control)

    launch = popen.call_args.kwargs
    if os.name == "nt":
        assert launch["creationflags"] & subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        assert launch["start_new_session"] is True


@pytest.mark.parametrize("terminal_status", ["cancelled", "timed_out"])
def test_cancel_and_timeout_terminate_the_process_tree(terminal_status: str) -> None:
    process = MagicMock(pid=4321, returncode=None)
    process.poll.return_value = None
    process.wait.return_value = 0
    control = MagicMock(scan_id=7, timeout=0 if terminal_status == "timed_out" else 30)
    control.cancel.is_set.return_value = False
    control.cancel.wait.return_value = terminal_status == "cancelled"

    with (
        patch.object(scan_control.subprocess, "Popen", return_value=process),
        patch.object(scan_control, "_terminate_process_tree") as terminate_tree,
        patch.object(scan_control, "_finish_if_active"),
        patch.object(scan_control, "release"),
    ):
        scan_control.supervise("/repo", control)

    terminate_tree.assert_called_once_with(process)


def test_oversized_worker_artifact_is_rejected_before_persistence(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_bytes(b"{}")
    process = MagicMock(pid=4321, returncode=0)
    process.poll.return_value = 0
    process.wait.return_value = 0
    control = MagicMock(scan_id=7, timeout=5)
    control.cancel.is_set.return_value = False

    with (
        patch.dict(os.environ, {"ECDAT_MAX_WORKER_RESULT_BYTES": "1"}),
        patch.object(scan_control.tempfile, "TemporaryDirectory") as temporary_directory,
        patch.object(scan_control.subprocess, "Popen", return_value=process),
        patch.object(scan_control, "_finish_if_active") as finish,
        patch.object(scan_control, "release"),
        patch("backend.services.scanner_runner.persist_scan_result") as persist,
    ):
        temporary_directory.return_value.__enter__.return_value = str(tmp_path)
        scan_control.supervise("/repo", control)

    persist.assert_not_called()
    assert finish.call_args.args[1] == "failed"


def test_progress_persistence_failure_does_not_abort_worker(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text('{"findings":[],"metrics":{}}', encoding="utf-8")
    (tmp_path / "progress.json").write_text('{"_phase":"collecting"}', encoding="utf-8")
    process = MagicMock(pid=4321, returncode=0)
    process.poll.side_effect = [None, 0, 0]
    process.wait.return_value = 0
    control = MagicMock(scan_id=7, timeout=30, durable_claim=False)
    control.cancel.is_set.return_value = False
    control.cancel.wait.return_value = False

    with (
        patch.object(scan_control.tempfile, "TemporaryDirectory") as temporary_directory,
        patch.object(scan_control.subprocess, "Popen", return_value=process),
        patch.object(scan_control, "_persist_worker_progress", side_effect=OSError("busy")),
        patch.object(scan_control, "_finish_if_active") as finish,
        patch.object(scan_control, "release"),
        patch("backend.services.scanner_runner.persist_scan_result"),
    ):
        temporary_directory.return_value.__enter__.return_value = str(tmp_path)
        scan_control.supervise("/repo", control)

    assert finish.call_args.args[1] == "completed"


def test_unexpected_worker_exit_is_retried_once(tmp_path: Path) -> None:
    (tmp_path / "result.json").write_text('{"findings":[],"metrics":{}}', encoding="utf-8")
    failed = MagicMock(pid=4321, returncode=1)
    failed.poll.return_value = 1
    failed.wait.return_value = 1
    succeeded = MagicMock(pid=4322, returncode=0)
    succeeded.poll.return_value = 0
    succeeded.wait.return_value = 0
    control = MagicMock(scan_id=7, timeout=30, durable_claim=False)
    control.cancel.is_set.return_value = False

    with (
        patch.object(scan_control.tempfile, "TemporaryDirectory") as temporary_directory,
        patch.object(scan_control.subprocess, "Popen", side_effect=[failed, succeeded]) as popen,
        patch.object(scan_control, "_finish_if_active") as finish,
        patch.object(scan_control, "release"),
        patch("backend.services.scanner_runner.persist_scan_result"),
    ):
        temporary_directory.return_value.__enter__.return_value = str(tmp_path)
        scan_control.supervise("/repo", control)

    assert popen.call_count == 2
    assert finish.call_args.args[1] == "completed"


def test_repeated_worker_exit_persists_the_exit_code(tmp_path: Path) -> None:
    first = MagicMock(pid=4321, returncode=-1073741819)
    first.poll.return_value = first.returncode
    first.wait.return_value = first.returncode
    second = MagicMock(pid=4322, returncode=-1073741819)
    second.poll.return_value = second.returncode
    second.wait.return_value = second.returncode
    control = MagicMock(scan_id=38, timeout=30, durable_claim=False)
    control.cancel.is_set.return_value = False

    with (
        patch.object(scan_control.tempfile, "TemporaryDirectory") as temporary_directory,
        patch.object(scan_control.subprocess, "Popen", side_effect=[first, second]),
        patch.object(scan_control, "_finish_if_active") as finish,
        patch.object(scan_control, "release"),
    ):
        temporary_directory.return_value.__enter__.return_value = str(tmp_path)
        scan_control.supervise("/repo", control)

    assert finish.call_args.args[1:] == (
        "failed",
        "Scan worker exited unexpectedly (code -1073741819); results are incomplete",
    )


def test_worker_diagnostic_returns_only_the_bounded_last_line(tmp_path: Path) -> None:
    diagnostic = tmp_path / "worker.stderr.log"
    diagnostic.write_text("first line\nValueError: collector crashed\n", encoding="utf-8")

    assert scan_control._worker_diagnostic(diagnostic) == "ValueError: collector crashed"


def test_real_timeout_terminates_spawned_descendant(tmp_path: Path) -> None:
    """The OS-backed termination path kills a worker's child, not just its root."""
    child_pid_path = tmp_path / "child.pid"
    parent_code = (
        "import pathlib,subprocess,sys,time;"
        "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)']);"
        "pathlib.Path(sys.argv[1]).write_text(str(child.pid));"
        "time.sleep(30)"
    )
    command = [sys.executable, "-c", parent_code, str(child_pid_path)]
    control = scan_control.Control(timeout=1, scan_id=7, cancel=threading.Event())

    with (
        patch.object(scan_control, "worker_command", return_value=command),
        patch.object(scan_control, "_finish_if_active") as finish,
        patch.object(scan_control, "release"),
    ):
        scan_control.supervise("/repo", control)

    assert finish.call_args.args[1] == "timed_out"
    assert child_pid_path.is_file()
    child_pid = int(child_pid_path.read_text())
    try:
        for _ in range(20):
            try:
                os.kill(child_pid, 0)
            except OSError:
                break
            time.sleep(0.05)
        else:
            pytest.fail("worker descendant remained alive after process-tree timeout")
    finally:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(child_pid), "/T", "/F"],
                capture_output=True,
                check=False,
            )
        else:
            try:
                os.kill(child_pid, 9)
            except OSError:
                pass
