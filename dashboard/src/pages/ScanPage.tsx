// New Scan - focused one-screen repository workflow.
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import {
  scanRepo,
  getScans,
  getScan,
  cancelScan,
  subscribeScanEvents,
  type ScanProgressEvent,
} from "../api/client";

const ACTIVE_SCAN_STATUSES = new Set(["started", "queued", "pending", "initialising", "running"]);
const TERMINAL_SCAN_STATUSES = new Set(["completed", "failed", "cancelled", "timed_out"]);
const ACTIVE_SCAN_KEY = "ecdat.activeScanId";

export const isActiveScanStatus = (status: string) => ACTIVE_SCAN_STATUSES.has(status);
export const shouldPollScanStatus = (status: string) => isActiveScanStatus(status);

export const progressDisplay = (event: ScanProgressEvent) => {
  const stats = event.collector_stats ?? {};
  const activityPhase = typeof stats._phase === "string" ? stats._phase : event.status;
  if (activityPhase === "indexing") {
    return {
      phase: activityPhase,
      files: typeof stats._files_discovered === "number" ? stats._files_discovered : 0,
      label: "Files discovered",
      total: undefined,
      progressPercent: undefined,
    };
  }
  const processed = typeof stats._files_processed === "number" ? stats._files_processed : undefined;
  const supported =
    typeof stats._files_supported === "number" ? stats._files_supported : event.in_scope_files;
  const progressPercent =
    typeof processed === "number" && typeof supported === "number" && supported > 0
      ? event.status === "completed"
        ? 100
        : processed < supported
          ? Math.round((processed / supported) * 10000) / 100
          : undefined
      : undefined;
  return {
    phase: activityPhase,
    files: processed ?? event.scanned_files ?? 0,
    label: "Files processed",
    total: typeof supported === "number" && supported > 0 ? supported : undefined,
    progressPercent,
    processingComplete:
      typeof processed === "number" &&
      typeof supported === "number" &&
      supported > 0 &&
      processed >= supported,
  };
};

const formatDuration = (ms: number) => {
  if (ms <= 0) return "0m 0s";
  const m = Math.floor(ms / 60000);
  const s = Math.floor((ms % 60000) / 1000);
  return `${m}m ${s}s`;
};

const serverTime = (value: string) =>
  Date.parse(/[zZ]|[+-]\d\d:\d\d$/.test(value) ? value : `${value}Z`);

export default function ScanPage() {
  const [repoPath, setRepoPath] = useState("");
  const [pathError, setPathError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [createError, setCreateError] = useState("");
  const [startError, setStartError] = useState<string | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [scan, setScan] = useState<{ scan_id: number; status: string } | null>(null);
  const [phase, setPhase] = useState<string>("pending");
  const [activityPhase, setActivityPhase] = useState<string>("pending");
  const [filesLabel, setFilesLabel] = useState("Files processed");
  const [filesProcessed, setFilesProcessed] = useState(0);
  const [filesTotal, setFilesTotal] = useState<number | undefined>();
  const [findingsCount, setFindingsCount] = useState(0);
  const [coveragePercent, setCoveragePercent] = useState<number | null>(null);
  const [processingComplete, setProcessingComplete] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [startedAt, setStartedAt] = useState<string | null>(null);
  const scanStartRef = useRef<string | null>(null);
  const [blindSpots, setBlindSpots] = useState<string[]>([]);
  const navigate = useNavigate();
  const repoInputId = useId();

  const [scans, setScans] = useState<import("../types").ScanJob[]>([]);
  useEffect(() => {
    getScans()
      .then(setScans)
      .catch(() => {});
  }, []);

  useEffect(() => {
    const saved = sessionStorage.getItem(ACTIVE_SCAN_KEY);
    const id = Number(saved);
    if (saved && Number.isSafeInteger(id) && id > 0) setScan({ scan_id: id, status: "pending" });
  }, []);

  const applyTiming = useCallback(
    (event: Pick<ScanProgressEvent, "status" | "started_at" | "finished_at">) => {
      if (!event.started_at || event.status === "pending" || event.status === "queued")
        return false;
      if (!Number.isFinite(serverTime(event.started_at))) return false;
      if (!scanStartRef.current) {
        scanStartRef.current = event.started_at;
        setStartedAt(event.started_at);
      }
      const end = event.finished_at ? serverTime(event.finished_at) : Date.now();
      setElapsed(Math.max(0, end - serverTime(scanStartRef.current)));
      return true;
    },
    [],
  );

  const applyProgress = useCallback(
    (event: ScanProgressEvent) => {
      setPhase(event.status);
      const display = progressDisplay(event);
      setActivityPhase(display.phase);
      setFilesProcessed(display.files);
      setFilesLabel(display.label);
      setFilesTotal(display.total);
      setFindingsCount(event.findings_count ?? event.assets_found ?? 0);
      setProcessingComplete(display.processingComplete ?? false);
      if (event.status === "completed") setCoveragePercent(100);
      else if (!display.processingComplete) setCoveragePercent(display.progressPercent ?? null);
      applyTiming(event);
      if (event.blind_spots) setBlindSpots(event.blind_spots);
      if (TERMINAL_SCAN_STATUSES.has(event.status)) sessionStorage.removeItem(ACTIVE_SCAN_KEY);
      if (TERMINAL_SCAN_STATUSES.has(event.status)) setCancelling(false);
    },
    [applyTiming],
  );

  useEffect(() => {
    if (!scan) return;
    let active = true;
    let sawEvent = false;
    let sawRunningEvent = false;
    let clockResolved = Boolean(scanStartRef.current);
    let loading = false;
    let poll: ReturnType<typeof setInterval> | undefined;
    let clockRetry: ReturnType<typeof setTimeout> | undefined;
    const retryClock = () => {
      if (!active || clockResolved || clockRetry || !sawRunningEvent) return;
      clockRetry = setTimeout(() => {
        clockRetry = undefined;
        void load();
      }, 1000);
    };
    const load = async () => {
      if (loading) return;
      loading = true;
      try {
        const job = await getScan(scan.scan_id);
        if (!active) return;
        setRepoPath(job.repo_path);
        clockResolved = applyTiming(job) || clockResolved;
        if (!sawEvent || poll || TERMINAL_SCAN_STATUSES.has(job.status)) {
          applyProgress({ ...job, scan_id: job.id });
        }
        if (TERMINAL_SCAN_STATUSES.has(job.status)) {
          sawRunningEvent = false;
          if (clockRetry) clearTimeout(clockRetry);
          clockRetry = undefined;
          stopEvents();
          if (poll) clearInterval(poll);
          poll = undefined;
        }
      } catch (error) {
        if (active)
          setStartError(error instanceof Error ? error.message : "Unable to load scan state");
      } finally {
        loading = false;
        retryClock();
      }
    };
    const startPolling = () => {
      if (!active || poll) return;
      void load();
      poll = setInterval(() => void load(), 1000);
    };
    const stopEvents = subscribeScanEvents(
      scan.scan_id,
      (event) => {
        if (active) {
          sawEvent = true;
          sawRunningEvent = event.status === "running";
          applyProgress(event);
          clockResolved = Boolean(scanStartRef.current);
          if (event.status === "running" && !clockResolved) void load();
        }
      },
      (event) => {
        if (active) {
          sawRunningEvent = false;
          applyProgress(event);
          if (!event.started_at || !event.finished_at) {
            void getScan(scan.scan_id)
              .then((job) => {
                if (active) applyProgress({ ...job, scan_id: job.id });
              })
              .catch(() => {});
          }
        }
      },
      () => startPolling(),
    );
    void load();
    return () => {
      active = false;
      stopEvents();
      if (poll) clearInterval(poll);
      if (clockRetry) clearTimeout(clockRetry);
    };
  }, [scan?.scan_id, applyProgress, applyTiming]);

  useEffect(() => {
    if (!startedAt || phase !== "running") return;
    const start = serverTime(startedAt);
    if (!Number.isFinite(start)) return;
    const timer = setInterval(() => setElapsed(Math.max(0, Date.now() - start)), 1000);
    return () => clearInterval(timer);
  }, [startedAt, phase]);

  const recentPaths = useMemo(() => {
    if (!scans.length) return [];
    const paths = Array.from(new Set(scans.map((s) => s.repo_path)));
    return paths.slice(0, 5);
  }, [scans]);

  const handlePathChange = (value: string) => {
    setRepoPath(value);
    setPathError("");
    if (value.trim()) {
      const trimmed = value.trim();
      if (!/^[A-Za-z]:\\/.test(trimmed) && !/^\//.test(trimmed)) {
        setPathError("Enter an absolute Windows or Linux path, e.g. C:\\repos\\my-app");
      } else if (trimmed.length < 3) {
        setPathError("Path is too short.");
      }
    }
  };

  const canProceedRepo = repoPath.trim().length > 0 && !pathError;

  const handleConfirmScan = async () => {
    setSubmitting(true);
    setCreateError("");
    try {
      const result = await scanRepo(repoPath.trim());
      sessionStorage.setItem(ACTIVE_SCAN_KEY, String(result.scan_id));
      setScan(result);
      setPhase(result.status);
      setActivityPhase(result.status);
      setFilesProcessed(0);
      setFilesTotal(undefined);
      setFindingsCount(0);
      setCoveragePercent(null);
      setProcessingComplete(false);
      setElapsed(0);
      setStartedAt(null);
      scanStartRef.current = null;
      setStartError(null);
      setCancelling(false);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : "Failed to start scan. Try again.");
    } finally {
      setSubmitting(false);
    }
  };

  const handleCancel = async () => {
    if (!scan) return;
    try {
      setCancelling(true);
      await cancelScan(scan.scan_id);
    } catch (error) {
      setCancelling(false);
      setStartError(error instanceof Error ? error.message : "Unable to cancel scan");
    }
  };

  useEffect(() => {
    if (scan && TERMINAL_SCAN_STATUSES.has(phase)) {
      const t = setTimeout(() => navigate(`/scans/${scan.scan_id}`), 3000);
      return () => clearTimeout(t);
    }
  }, [scan, phase, navigate]);

  const isScanning = scan && isActiveScanStatus(phase);

  if (scan && TERMINAL_SCAN_STATUSES.has(phase)) {
    const isComplete = phase === "completed";
    return (
      <div className="scan-completed">
        <div className="scan-completed-icon" aria-hidden="true">
          {isComplete ? (
            <svg
              width="48"
              height="48"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <polyline points="20 6 9 17 4 12" />
            </svg>
          ) : (
            <svg
              width="48"
              height="48"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
            >
              <circle cx="12" cy="12" r="10" />
              <line x1="12" y1="8" x2="12" y2="12" />
              <line x1="12" y1="16" x2="12.01" y2="16" />
            </svg>
          )}
        </div>
        <h2>
          {isComplete
            ? "Scan completed"
            : phase === "cancelled"
              ? "Scan cancelled"
              : phase === "timed_out"
                ? "Scan timed out"
                : "Scan failed"}
        </h2>
        <p>
          {isComplete
            ? `Found ${findingsCount} finding${findingsCount !== 1 ? "s" : ""} in ${filesProcessed.toLocaleString()} file${filesProcessed !== 1 ? "s" : ""} after ${formatDuration(elapsed)}.`
            : phase === "cancelled"
              ? "The scan was cancelled."
              : startError || "An unexpected error occurred."}
        </p>
        {isComplete && (
          <div
            className="scan-progress"
            role="progressbar"
            aria-valuenow={100}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-label="File processing progress"
          >
            <div className="scan-progress-bar" style={{ width: "100%" }} />
          </div>
        )}
        <div className="scan-completed-actions">
          <Link className="button primary" to={`/scans/${scan.scan_id}`}>
            {isComplete ? "View results" : "View details"}
          </Link>
          <button
            className="button"
            onClick={() => {
              setScan(null);
              setPhase("pending");
            }}
          >
            {isComplete ? "New scan" : "Try again"}
          </button>
        </div>
      </div>
    );
  }

  if (isScanning) {
    const statusLabel =
      activityPhase === "started" ||
      activityPhase === "queued" ||
      activityPhase === "pending" ||
      activityPhase === "initialising"
        ? "Initialising…"
        : activityPhase.replace(/_/g, " ").replace(/\b\w/g, (l: string) => l.toUpperCase());
    return (
      <div className="scan-running">
        <h2>Scanning repository</h2>
        <div className="scan-running-meta">
          <span className="scan-running-repo">{repoPath}</span>
          <span className="scan-running-elapsed">{formatDuration(elapsed)}</span>
        </div>
        <div
          className="scan-progress"
          role="progressbar"
          aria-valuenow={processingComplete ? undefined : (coveragePercent ?? undefined)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label="File processing progress"
        >
          <div className="scan-progress-bar" style={{ width: `${coveragePercent ?? 0}%` }} />
        </div>
        <div className="scan-running-stats">
          <div>
            <span className="scan-stat-value">{statusLabel}</span>
            <span className="scan-stat-label">Phase</span>
          </div>
          <div>
            <span className="scan-stat-value">
              {filesProcessed.toLocaleString()}
              {filesTotal !== undefined ? ` / ${filesTotal.toLocaleString()}` : ""}
            </span>
            <span className="scan-stat-label">{filesLabel}</span>
          </div>
          <div>
            <span className="scan-stat-value">{findingsCount}</span>
            <span className="scan-stat-label">Findings</span>
          </div>
          <div>
            <span className="scan-stat-value">
              {processingComplete
                ? "Finalising"
                : coveragePercent !== null
                  ? `${coveragePercent}%`
                  : "Determining scope"}
            </span>
            <span className="scan-stat-label">Live scan progress</span>
          </div>
        </div>
        {startError && (
          <p className="scan-input-error" role="alert">
            {startError}
          </p>
        )}
        {blindSpots.length > 0 && (
          <div className="scan-blind-spots" role="note" aria-label="Declared blind spots">
            <strong>Declared blind spots:</strong>
            <ul>
              {blindSpots.map((b) => (
                <li key={b}>{b}</li>
              ))}
            </ul>
          </div>
        )}
        <button className="button ghost" onClick={handleCancel} disabled={cancelling}>
          {cancelling ? "Cancelling…" : "Cancel scan"}
        </button>
      </div>
    );
  }

  return (
    <div className="scan-launch-page">
      <header className="scan-launch-header">
        <div>
          <h1>Scan a repository</h1>
          <p>Enter a local repository path and start an evidence-backed scan.</p>
        </div>
        <div className="scan-safety-note" role="note">
          <strong>Read-only analysis</strong>
          <span>Source files are inspected, never changed.</span>
        </div>
      </header>

      <section className="scan-launch-card" role="group" aria-labelledby="scan-repo-heading">
        <div className="scan-launch-main">
          <div className="scan-launch-title">
            <div>
              <h2 id="scan-repo-heading">Repository path</h2>
              <p>Windows and Linux absolute paths are supported.</p>
            </div>
          </div>
          <label className="scan-input-label" htmlFor={repoInputId}>
            Repository path
          </label>
          <div className="scan-path-row">
            <input
              id={repoInputId}
              type="text"
              className="scan-input"
              value={repoPath}
              onChange={(e) => handlePathChange(e.target.value)}
              placeholder="C:\\repos\\my-application"
              aria-invalid={Boolean(pathError)}
              aria-describedby={pathError ? "scan-path-error" : "scan-path-hint"}
              autoFocus
            />
            <button
              className="button primary scan-start-button"
              disabled={!canProceedRepo || submitting}
              onClick={handleConfirmScan}
            >
              {submitting ? "Starting…" : "Start scan"}
            </button>
          </div>
          {pathError && (
            <p id="scan-path-error" className="scan-input-error" role="alert">
              {pathError}
            </p>
          )}
          <p id="scan-path-hint" className="scan-input-hint">
            The path must be accessible to the ECDAT scanner service.
          </p>

          {recentPaths.length > 0 && (
            <div className="scan-recent">
              <span className="scan-recent-label">Recent paths</span>
              <div className="scan-recent-list" role="list">
                {recentPaths.map((p: string) => (
                  <button
                    key={p}
                    type="button"
                    className="scan-recent-chip"
                    onClick={() => handlePathChange(p)}
                    role="listitem"
                    title={p}
                  >
                    <svg
                      width="14"
                      height="14"
                      viewBox="0 0 24 24"
                      fill="none"
                      stroke="currentColor"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      aria-hidden="true"
                    >
                      <path d="M22 19a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h5l2 3h9a2 2 0 0 1 2 2z" />
                    </svg>
                    <span>{p.split(/[\\/]/).filter(Boolean).pop() || p}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </section>
      {createError && (
        <div className="alert alert--error" role="alert">
          {createError}
        </div>
      )}
      <section className="scan-expectations" aria-label="What this scan produces">
        <p className="scan-aside-label">What you will get</p>
        <div>
          <span>
            <strong>Inventory</strong> Algorithms, libraries, keys, and certificates
          </span>
          <span>
            <strong>Risk context</strong> Medium and Low remain distinct from urgent findings
          </span>
          <span>
            <strong>Quality evidence</strong> Supported-file coverage always; evaluation precision
            only with ground truth
          </span>
        </div>
      </section>
    </div>
  );
}
