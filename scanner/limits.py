"""Bound local reads; these limits are not a filesystem security sandbox."""
import importlib
import os
import stat
from types import ModuleType

resource: ModuleType | None

try:
    resource = importlib.import_module("resource")
except ImportError:  # Windows has no stdlib resource module.
    resource = None


def positive_int(name: str, default: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1 or value > maximum:
        raise ValueError(f"Invalid {name}")
    return value


def nonnegative_int(name: str, default: int, maximum: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 0 or value > maximum:
        raise ValueError(f"Invalid {name}")
    return value


def max_file_bytes() -> int:
    return positive_int('ECDAT_MAX_FILE_BYTES', 8 * 1024 * 1024, 128 * 1024 * 1024)


def max_evidence_count() -> int:
    """Bound aggregate evidence retained before correlation and persistence."""
    return positive_int('ECDAT_MAX_EVIDENCE', 100000, 1000000)


def scan_duration_budget_ms() -> int:
    """Maximum wall-clock time allowed for a single scan. 0 means unlimited."""
    return nonnegative_int('ECDAT_SCAN_DURATION_BUDGET_MS', 0, 3_600_000)


def scan_memory_budget_mb() -> int:
    """Approximate RSS memory ceiling for a scan process. 0 means unlimited."""
    return nonnegative_int('ECDAT_SCAN_MEMORY_BUDGET_MB', 512, 4096)


def check_memory_budget() -> str | None:
    """Return failure code if process RSS exceeds budget, else None."""
    budget = scan_memory_budget_mb()
    if budget <= 0:
        return None
    try:
        if resource is None:
            return None
        usage = resource.getrusage(resource.RUSAGE_SELF)
        rss_kb = usage.ru_maxrss
        rss_mb = rss_kb / 1024
        if rss_mb > budget:
            return "memory_budget_exceeded"
    except Exception:
        pass
    return None


def read_bytes(path: str) -> bytes:
    limit = max_file_bytes()
    if os.path.islink(path):
        raise OSError('Linked files are outside scan scope')
    with open(path, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise OSError('Unsupported or oversized scan file')
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise OSError('Scan file grew beyond configured limit')
    return data


def read_text(path: str, errors: str = "strict") -> str:
    return read_bytes(path).decode('utf-8', errors=errors)
