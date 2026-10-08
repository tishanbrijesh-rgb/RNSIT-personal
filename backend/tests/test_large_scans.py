"""Stage 3 - large-scan safety tests.

Creates controlled fixture directories and verifies that the scanner:
  - Respects file-count limits
  - Respects file-size limits (oversized -> 'oversized' failure)
  - Handles unreadable files (-> 'unreadable')
  - Skips symlinks
  - Records metrics (total_files, in_scope_files, scanned_files, failed_files,
    coverage_pct, blind_spots, failures)
  - Produces deterministic results on repeated scans of the same fixture
"""
from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
from pathlib import Path

import pytest

# -- Fixtures ----------------------------------------------------------------


def _make_root(name: str) -> Path:
    tmpdir = tempfile.mkdtemp()
    root = Path(tmpdir) / name
    root.mkdir()
    return root


@pytest.fixture()
def small_fixture() -> Path:
    """~10 supported files with known crypto content."""
    root = _make_root("small_repo")
    content = (
        "import hashlib\n"
        "from cryptography.hazmat.primitives.ciphers import Cipher\n"
    )
    for name in [
        "main.py", "utils.py", "config.py", "auth.py", "models.py",
        "api.py", "worker.py", "tests.py", "setup.py", "README.md",
    ]:
        (root / name).write_text(content, encoding="utf-8")
    yield root
    shutil.rmtree(str(root.parent), ignore_errors=True)


@pytest.fixture()
def nested_fixture() -> Path:
    """Nested directories with ignored dirs (node_modules, .git, etc.)."""
    root = _make_root("nested_repo")
    for d in ["src", "src/core", "src/plugins", "tests", "docs"]:
        (root / d).mkdir(parents=True)

    for rel in [
        "src/main.py", "src/core/crypto.py", "src/plugins/tls.py",
        "tests/test_encrypt.py", "docs/guide.md",
    ]:
        (root / rel).write_text(
            "import ssl\nimport rsa\nprint('RSA-2048 key here')\n",
            encoding="utf-8",
        )

    # ignored directories should not appear in results
    (root / "node_modules/fake").mkdir(parents=True)
    (root / "node_modules/fake/index.js").write_text(
        "var crypto = require('crypto');\n"
    )
    (root / ".git").mkdir()
    (root / ".git/config").write_text("[core]\n")
    (root / "__pycache__").mkdir()
    (root / "__pycache__/mod.cpython-313.pyc").write_bytes(b"\x00" * 100)
    yield root
    shutil.rmtree(str(root.parent), ignore_errors=True)


@pytest.fixture()
def medium_fixture() -> Path:
    """500 supported text files with simple crypto references."""
    root = _make_root("medium_repo")
    content = (
        "from cryptography.hazmat.primitives.asymmetric import rsa, padding\n"
    )
    for i in range(500):
        (root / f"file_{i:04d}.py").write_text(content, encoding="utf-8")
    yield root
    shutil.rmtree(str(root.parent), ignore_errors=True)


@pytest.fixture()
def fixture_with_oversized() -> Path:
    """Files including one that exceeds the default 8 MB byte limit."""
    root = _make_root("size_repo")
    (root / "normal.py").write_text("import hashlib\n", encoding="utf-8")
    (root / "huge.py").write_bytes(b"X" * (9 * 1024 * 1024))  # 9 MB
    yield root
    shutil.rmtree(str(root.parent), ignore_errors=True)


@pytest.fixture()
def fixture_with_unreadable() -> Path:
    """Files including one with no read permissions."""
    root = _make_root("perm_repo")
    (root / "good.py").write_text("import hashlib\n", encoding="utf-8")
    bad = root / "bad.py"
    bad.write_text("import hashlib\n", encoding="utf-8")
    if os.name == "nt":
        pytest.skip("File permission tests require POSIX (chmod)")
    os.chmod(str(bad), 0o000)
    try:
        yield root
    finally:
        os.chmod(str(bad), stat.S_IWUSR | stat.S_IRUSR)
        shutil.rmtree(str(root.parent), ignore_errors=True)


# -- Tests -------------------------------------------------------------------


class TestScanFixtures:
    """Scanner behavior on controlled fixture directories."""

    def test_inventory_reports_discovered_files_before_collection(self, tmp_path: Path):
        """Directory walking emits useful progress before collectors start."""
        from scanner.main import scan_with_metrics

        for index in range(3):
            (tmp_path / f"module_{index}.py").write_text("import hashlib\n", encoding="utf-8")

        events: list[dict] = []
        scan_with_metrics(str(tmp_path), progress_callback=events.append)

        indexing = [event for event in events if event.get("_phase") == "indexing"]
        assert indexing
        assert indexing[-1]["_files_discovered"] == 3
        assert indexing[-1]["_files_supported"] == 3
        collecting = [event for event in events if event.get("_phase") == "collecting"]
        assert collecting[-1]["_files_discovered"] == 3
        assert collecting[-1]["_files_supported"] == 3

    def test_worker_reports_correlated_findings_from_real_scan(self, tmp_path: Path):
        from backend.services.scanner_runner import collect_scan_result

        (tmp_path / "crypto.py").write_text(
            "from Crypto.PublicKey import RSA\nkey = RSA.generate(2048)\n", encoding="utf-8"
        )
        events: list[dict] = []
        result = collect_scan_result(str(tmp_path), progress_callback=events.append)

        assert any(event.get("_phase") == "indexing" for event in events)
        assert any(event.get("_phase") == "collecting" for event in events)
        assert any(event.get("_phase") == "correlating" for event in events)
        assert events[-1]["_phase"] == "persisting"
        assert events[-1]["_findings_count"] == len(result["findings"])

    def test_unexpected_collector_error_isolated_to_one_file(self, tmp_path: Path):
        """A malformed file must not terminate the repository-wide scan."""
        from scanner.main import _collect_path

        path = str(tmp_path / "broken.py")

        class BrokenRegistry:
            def handlers_for(self, _path):
                def crash(_file, on_error=None):
                    raise RuntimeError("unexpected parser failure")
                return [("ast", crash)]

        failed_paths: set[str] = set()
        failure_codes: dict[str, str] = {}
        evidence_count = _collect_path(
            path, BrokenRegistry(), {}, {"ast": 0}, failed_paths,
            failure_codes, 0, 100,
        )

        assert evidence_count == 0
        assert failed_paths == {path}
        assert failure_codes[path] == "parse_error"

    def test_small_fixture_completes(self, small_fixture: Path):
        """10-file fixture produces evidence and metrics."""
        from scanner.main import scan_with_metrics
        _evidence, metrics = scan_with_metrics(str(small_fixture))
        assert metrics["in_scope_files"] > 0
        assert metrics["total_files"] >= metrics["in_scope_files"]
        assert metrics["scanned_files"] >= 0
        assert 0.0 <= metrics["coverage_pct"] <= 100.0

    def test_nested_fixture_excludes_ignored_dirs(self, nested_fixture: Path):
        """node_modules, .git, __pycache__ are not in in_scope_files."""
        from scanner.main import scan_with_metrics
        _, metrics = scan_with_metrics(str(nested_fixture))
        assert metrics["in_scope_files"] == 4
        failure_paths = [f["path"] for f in metrics["failures"]]
        for ign in ("node_modules", ".git", "__pycache__"):
            assert not any(ign in p for p in failure_paths)

    def test_oversized_file_recorded_as_failure(self, fixture_with_oversized: Path, monkeypatch):
        """File exceeding byte limit appears in failures with reason 'oversized'."""
        from scanner.main import scan_with_metrics
        monkeypatch.setenv("ECDAT_MAX_FILE_BYTES", str(8 * 1024 * 1024))
        _, metrics = scan_with_metrics(str(fixture_with_oversized))
        failure_reasons = {f["reason"] for f in metrics["failures"]}
        assert "oversized" in failure_reasons
        assert metrics["in_scope_files"] >= 1

    def test_unreadable_file_recorded_as_failure(self, fixture_with_unreadable: Path):
        """File with no read permissions appears as 'unreadable'."""
        from scanner.main import scan_with_metrics
        _, metrics = scan_with_metrics(str(fixture_with_unreadable))
        failure_reasons = {f["reason"] for f in metrics["failures"]}
        assert "unreadable" in failure_reasons

    def test_symlink_excluded(self, fixture_with_symlink: Path):
        """Symlinked files are not double-counted."""
        from scanner.main import scan_with_metrics
        _, metrics = scan_with_metrics(str(fixture_with_symlink))
        assert metrics["in_scope_files"] == 1

    def test_metrics_keys_present(self, small_fixture: Path):
        """Every scan returns all required metric keys."""
        from scanner.main import scan_with_metrics
        _, metrics = scan_with_metrics(str(small_fixture))
        for key in (
            "total_files", "in_scope_files", "scanned_files",
            "failed_files", "coverage_pct", "duration_ms",
            "collector_stats", "blind_spots", "failures",
        ):
            assert key in metrics, f"Missing metric key: {key}"

    def test_deterministic_repeat_scan(self, small_fixture: Path):
        """Scanning the same fixture twice produces identical counts."""
        from scanner.main import scan_with_metrics
        _, m1 = scan_with_metrics(str(small_fixture))
        _, m2 = scan_with_metrics(str(small_fixture))
        assert m1["in_scope_files"] == m2["in_scope_files"]
        assert m1["total_files"] == m2["total_files"]
        assert m1["scanned_files"] == m2["scanned_files"]

    def test_counters_are_monotonic(self, small_fixture: Path):
        """total_files >= in_scope_files >= scanned_files + failed_files."""
        from scanner.main import scan_with_metrics
        _, metrics = scan_with_metrics(str(small_fixture))
        processed = metrics["scanned_files"] + metrics["failed_files"]
        assert metrics["total_files"] >= metrics["in_scope_files"]
        assert metrics["in_scope_files"] >= processed

    def test_medium_fixture_completes_within_time(self, medium_fixture: Path):
        """500-file fixture finishes without hanging."""
        from scanner.main import scan_with_metrics
        t0 = time.perf_counter()
        _, metrics = scan_with_metrics(str(medium_fixture))
        elapsed = time.perf_counter() - t0
        assert metrics["total_files"] == 500
        assert metrics["in_scope_files"] == 500
        assert elapsed < 60, f"500-file scan took {elapsed:.1f}s"

    def test_evidence_count_within_limit(self, medium_fixture: Path):
        """Evidence records stay below the 100K default limit."""
        from scanner.main import scan_with_metrics
        _, metrics = scan_with_metrics(str(medium_fixture))
        evidence_total = sum(
            v for v in metrics["collector_stats"].values() if isinstance(v, int)
        )
        assert evidence_total < 100_000


@pytest.fixture()
def fixture_with_symlink() -> Path:
    """Directory with a symlink to a regular file."""
    root = _make_root("link_repo")
    target = root / "real.py"
    target.write_text("import hashlib\n", encoding="utf-8")
    link = root / "link_to_real.py"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Symlinks not supported on this platform")
    yield root
    shutil.rmtree(str(root.parent), ignore_errors=True)
