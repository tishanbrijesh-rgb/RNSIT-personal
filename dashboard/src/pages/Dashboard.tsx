// Portfolio posture, assurance measurements, and research evaluation.
import { lazy, Suspense, useEffect, useState, memo, useRef, useCallback, useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { motion, type Variants } from "framer-motion";
import {
  downloadReport,
  getDashboardSummary,
  getEvaluation,
  canWrite,
  getScans,
  getAssets,
  scanRepo,
} from "../api/client";
import type { DashboardSummary, Evaluation, CryptoAsset, ScanJob } from "../types";
import { RiskBadge } from "../components/RiskBadge";
import { SkeletonCard, SkeletonTable } from "../components/Skeletons";
import { useToast } from "../components/Toast";
import { displayPath, relativeTime, repositoryName } from "../utils/format";

// ── Stagger animation variants ──────────────────────────────────
const staggerContainer: Variants = {
  animate: { transition: { staggerChildren: 0.06, delayChildren: 0.05 } },
};

const listStagger: Variants = {
  animate: { transition: { staggerChildren: 0.04 } },
};

const staggerItem: Variants = {
  initial: { opacity: 0, y: 14 },
  animate: {
    opacity: 1,
    y: 0,
    transition: { duration: 0.35, ease: [0.25, 0.1, 0.25, 1] },
  },
};

const RiskDistributionChart = lazy(() => import("../components/RiskDistributionChart"));

const SCAN_STATUS_FILTERS = ["all", "completed", "failed", "cancelled", "running"] as const;
type ScanStatusFilter = (typeof SCAN_STATUS_FILTERS)[number];

// ── Animated counter ────────────────────────────────────────────
const AnimatedNumber = memo(function AnimatedNumber({
  value,
  suffix = "",
}: {
  value: number;
  suffix?: string;
}) {
  const [display, setDisplay] = useState(0);
  const rafRef = useRef<number>(0);
  const displayRef = useRef(0);

  useEffect(() => {
    // Skip animation when reduced motion is preferred
    if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
      setDisplay(value);
      displayRef.current = value;
      return;
    }

    // Cancel any in-progress animation
    cancelAnimationFrame(rafRef.current);

    // If already at target, don't animate
    if (displayRef.current === value) {
      setDisplay(value);
      return;
    }

    const duration = 600;
    const start = performance.now();
    const from = displayRef.current;

    const step = (now: number) => {
      const t = Math.min(1, (now - start) / duration);
      const eased = 1 - Math.pow(1 - t, 3);
      const next = Math.round(from + (value - from) * eased);
      const clamped = Math.max(0, next); // never negative
      displayRef.current = clamped;
      setDisplay(clamped);
      if (t < 1) rafRef.current = requestAnimationFrame(step);
    };

    rafRef.current = requestAnimationFrame(step);
    return () => cancelAnimationFrame(rafRef.current);
  }, [value]);
  return (
    <>
      {display}
      {suffix}
    </>
  );
});

// ── Sub-row: Posture Summary ────────────────────────────────────
const PostureRow = memo(function PostureRow({
  summary,
  scans,
}: {
  summary: DashboardSummary;
  scans: ScanJob[];
}) {
  const rd = summary.risk_distribution;
  const total = summary.total_assets;
  const urgent = rd.CRITICAL + rd.HIGH;
  const onlyModerateRisk = urgent === 0 && (rd.MEDIUM > 0 || rd.LOW > 0);

  // Trend: compare current total with previous completed scan
  // The summary itself carries the latest scan's totals; previous is from the second recent completed
  const trend = (() => {
    // Compare total assets in current summary vs inferred previous
    // Use the scans list to find the prior completed scan
    const sortedCompleted = scans
      .filter((s) => s.status === "completed")
      .sort((a, b) => b.id - a.id);
    if (sortedCompleted.length < 2) return null;
    const latest = sortedCompleted[0];
    const previous = sortedCompleted[1];
    if (latest.id !== summary.latest_scan_id) return null;
    // Use assets_found as proxy for total_assets delta
    const delta = (latest.assets_found || 0) - (previous.assets_found || 0);
    if (delta === 0) return { direction: "stable" as const, delta: 0 };
    return { direction: delta > 0 ? "up" : "down", delta: Math.abs(delta) };
  })();

  return (
    <motion.section className="dashboard-posture" variants={staggerItem}>
      <div className="posture-main">
        <span className="posture-label">Total assets in scope</span>
        <strong className="posture-number">
          <AnimatedNumber value={total} />
          {trend && (
            <span className={"posture-trend posture-trend--" + trend.direction}>
              {trend.direction === "up" && <span className="trend-arrow">&#9650;</span>}
              {trend.direction === "down" && <span className="trend-arrow">&#9660;</span>}
              {trend.direction === "stable" && <span className="trend-arrow">&#9644;</span>}
              <span className="trend-delta">
                {trend.delta > 0 ? "+" : ""}
                {trend.delta}
              </span>
            </span>
          )}
        </strong>
      </div>
      <div
        className="posture-risk-bar"
        role="img"
        aria-label={
          "Risk breakdown: " +
          rd.CRITICAL +
          " critical, " +
          rd.HIGH +
          " high, " +
          rd.MEDIUM +
          " medium, " +
          rd.LOW +
          " low"
        }
      >
        {(["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const).map((label) => {
          const count = rd[label];
          if (count === 0) return null;
          return (
            <div
              key={label}
              className={"posture-segment posture-segment-" + label.toLowerCase()}
              style={{ flexGrow: count }}
              title={label + ": " + count}
            >
              {total > 0 && (count / total) * 100 >= 12 ? <span>{count}</span> : null}
            </div>
          );
        })}
      </div>
      <div className="posture-chips">
        <span className="posture-chip posture-chip-urgent">
          <AnimatedNumber value={urgent} /> urgent
        </span>
        <span className="posture-chip posture-chip-quantum">
          <AnimatedNumber value={summary.quantum_vulnerable_count} /> quantum-exposed
        </span>
        {summary.conflict_count > 0 && (
          <span className="posture-chip posture-chip-conflict">
            <AnimatedNumber value={summary.conflict_count} /> conflict
            {summary.conflict_count === 1 ? "" : "s"}
          </span>
        )}
      </div>
      {onlyModerateRisk && (
        <div className="posture-explainer" role="status">
          <strong>No Critical or High findings</strong>
          <span>
            This scan contains {rd.MEDIUM} Medium and {rd.LOW} Low findings. Medium items still need
            context review; this is not a blanket safety claim.
          </span>
        </div>
      )}
    </motion.section>
  );
});

// ── Sub-row: Urgent Actions ─────────────────────────────────────
const UrgentActions = memo(function UrgentActions({ summary }: { summary: DashboardSummary }) {
  const scanQuery = "?scan_id=" + summary.latest_scan_id;
  const actions: Array<{ label: string; count: number; href: string; tone: string }> = [];

  const rd = summary.risk_distribution;
  if (rd.CRITICAL > 0) {
    actions.push({
      label: "Critical assets need PQC migration",
      count: rd.CRITICAL,
      href: "/assets" + scanQuery + "&risk=CRITICAL",
      tone: "red",
    });
  }
  if (rd.HIGH > 0) {
    actions.push({
      label: "High-risk assets need review",
      count: rd.HIGH,
      href: "/assets" + scanQuery + "&risk=HIGH",
      tone: "amber",
    });
  }
  if (summary.quantum_vulnerable_count > 0 && rd.CRITICAL === 0 && rd.HIGH === 0) {
    actions.push({
      label: "Quantum-exposed assets without high risk rating",
      count: summary.quantum_vulnerable_count,
      href: "/assets" + scanQuery + "&quantum=true",
      tone: "amber",
    });
  }
  if (summary.conflict_count > 0) {
    actions.push({
      label: "Evidence conflicts to resolve",
      count: summary.conflict_count,
      href: "/assets" + scanQuery,
      tone: "violet",
    });
  }
  if (summary.coverage_pct < 100) {
    const blindCount = summary.blind_spots.length;
    actions.push({
      label:
        "Supported-file coverage at " +
        summary.coverage_pct +
        "% — " +
        blindCount +
        " visibility gap" +
        (blindCount === 1 ? "" : "s"),
      count: summary.coverage_pct,
      href: "/scan",
      tone: "blue",
    });
  }

  return (
    <motion.section className="dashboard-urgent" variants={staggerItem}>
      <h2>Urgent actions</h2>
      {actions.length === 0 ? (
        <p className="muted">
          {rd.MEDIUM > 0
            ? `No Critical or High findings. Review ${rd.MEDIUM} Medium finding${rd.MEDIUM === 1 ? "" : "s"} for missing business context.`
            : "No urgent actions. Current findings are Low risk and fully covered."}
        </p>
      ) : (
        <motion.ul
          className="urgent-list"
          variants={listStagger}
          initial="initial"
          animate="animate"
        >
          {actions.slice(0, 5).map((a) => (
            <motion.li key={a.label} variants={staggerItem}>
              <Link to={a.href} className={"urgent-link urgent-tone-" + a.tone}>
                <span className="urgent-count">{a.count}</span>
                <span className="urgent-label">{a.label}</span>
                <span className="urgent-arrow" aria-hidden="true">
                  &rarr;
                </span>
              </Link>
            </motion.li>
          ))}
        </motion.ul>
      )}
    </motion.section>
  );
});

// ── Sub-row: Assurance Quality ──────────────────────────────────
const AssuranceQuality = memo(function AssuranceQuality({
  summary,
  evaluation,
}: {
  summary: DashboardSummary;
  evaluation?: Evaluation;
}) {
  const confPct = Math.round(summary.avg_confidence * 100);
  const confTone = confPct >= 80 ? "teal" : confPct >= 50 ? "amber" : "red";
  const collectorEntries = Object.entries(summary.collector_stats ?? {}).filter(
    ([label, count]) => !label.startsWith("_") && typeof count === "number",
  );

  return (
    <motion.section className="dashboard-assurance" variants={staggerItem}>
      <h2>Assurance quality</h2>
      <div className="assurance-metrics">
        <div className={"assurance-metric tone-" + confTone}>
          <strong>{confPct}%</strong>
          <span>Avg evidence confidence</span>
          <div className="assurance-bar-track">
            <div className="assurance-bar-fill" style={{ width: confPct + "%" }} />
          </div>
        </div>
        <div className="assurance-metric tone-teal">
          <strong>{summary.coverage_pct}%</strong>
          <span>Supported-file coverage</span>
          <div className="assurance-bar-track">
            <div className="assurance-bar-fill" style={{ width: summary.coverage_pct + "%" }} />
          </div>
        </div>
        {evaluation && evaluation.available && (
          <>
            <div className="assurance-metric tone-blue">
              <strong>{Math.round((evaluation.precision || 0) * 100)}%</strong>
              <span>Corpus precision</span>
            </div>
            <div className="assurance-metric tone-blue">
              <strong>{Math.round((evaluation.recall || 0) * 100)}%</strong>
              <span>Corpus recall</span>
            </div>
            <div className="assurance-metric tone-indigo">
              <strong>{Math.round((evaluation.f1 || 0) * 100)}%</strong>
              <span>Corpus F1 score</span>
            </div>
          </>
        )}
        {(!evaluation || !evaluation.available) && (
          <div className="assurance-metric assurance-metric--unavailable tone-neutral">
            <strong>Not measured</strong>
            <span>Evaluation-corpus precision / recall</span>
            <small>Add a valid ground_truth.json to evaluate this scan.</small>
          </div>
        )}
      </div>
      {collectorEntries.length > 0 && (
        <div className="assurance-collectors">
          <span className="assurance-collectors-label">Corpus provenance</span>
          <div className="assurance-collector-chips">
            {collectorEntries.map(([label, count]) => (
              <span key={label} className="assurance-collector-chip">
                {label.toUpperCase()}: {count}
              </span>
            ))}
          </div>
        </div>
      )}
    </motion.section>
  );
});

// ── Sub-row: Needs Attention ─────────────────────────────────────
const NeedsAttention = memo(function NeedsAttention({
  assets,
  scanId,
}: {
  assets: CryptoAsset[];
  scanId?: number;
}) {
  if (assets.length === 0) {
    return (
      <motion.section className="dashboard-attention" variants={staggerItem}>
        <h2>Needs attention</h2>
        <p className="attention-empty">No critical or high-risk findings. Posture is strong.</p>
      </motion.section>
    );
  }

  const scanQuery = scanId ? "?scan_id=" + scanId : "";

  return (
    <motion.section className="dashboard-attention" variants={staggerItem}>
      <h2>Needs attention</h2>
      <div className="attention-list">
        {assets.map((a) => {
          const reason = a.risk_reasons?.[0] || a.evidence_json?.reasons?.[0] || "No detail";
          const location = a.location || "unknown";
          const confidence = Math.round((a.confidence ?? 0) * 100);
          return (
            <Link
              key={a.id}
              to={`/assets/${a.id}${scanQuery}`}
              className={"attention-row attention-row--" + a.priority_label.toLowerCase()}
            >
              <div className="attention-row-main">
                <RiskBadge label={a.priority_label} score={a.priority_score} size="sm" />
                <span className="attention-algorithm">{a.algorithm}</span>
              </div>
              <div className="attention-row-detail">
                <span className="attention-location" title={location}>
                  {displayPath(location)}
                </span>
                <span className="attention-reason">{reason}</span>
                <span className="attention-meta">
                  {confidence}% evidence confidence &middot; {a.category}
                </span>
              </div>
            </Link>
          );
        })}
      </div>
    </motion.section>
  );
});

// ── Sub-row: Recent Scans ────────────────────────────────────────
const RecentScans = memo(function RecentScans({
  scans,
  scanStatusFilter,
  onStatusFilterChange,
  scanSearch,
  onScanSearchChange,
  onRescan,
  scanningRepo,
}: {
  scans: ScanJob[];
  scanStatusFilter: ScanStatusFilter;
  onStatusFilterChange: (v: ScanStatusFilter) => void;
  scanSearch: string;
  onScanSearchChange: (v: string) => void;
  onRescan: (repoPath: string) => void;
  scanningRepo: string;
}) {
  const statusClass = (s: string) => {
    if (s === "completed") return "recent-status--ok";
    if (s === "failed") return "recent-status--err";
    if (s === "cancelled") return "recent-status--warn";
    return "recent-status--running";
  };

  return (
    <motion.section className="dashboard-recent" variants={staggerItem}>
      <div className="recent-header">
        <h2>Recent scans</h2>
        <div className="recent-filters">
          {SCAN_STATUS_FILTERS.map((f) => (
            <button
              key={f}
              className={`recent-filter-btn${scanStatusFilter === f ? " active" : ""}`}
              onClick={() => onStatusFilterChange(f)}
              aria-pressed={scanStatusFilter === f}
            >
              {f === "all"
                ? "All"
                : f === "completed"
                  ? "Complete"
                  : f === "failed"
                    ? "Failed"
                    : f === "cancelled"
                      ? "Cancelled"
                      : "Running"}
            </button>
          ))}
          <input
            type="text"
            className="recent-search"
            placeholder="Search repo…"
            value={scanSearch}
            onChange={(e) => onScanSearchChange(e.target.value)}
          />
        </div>
      </div>
      {scans.length === 0 ? (
        <p className="recent-empty">No scans match the current filter.</p>
      ) : (
        <div className="recent-list">
          {scans.map((s) => {
            const dur = s.duration_ms ? Math.round(s.duration_ms / 1000) : null;
            return (
              <div key={s.id} className="recent-row">
                <Link to={`/scans/${s.id}`} className="recent-row-main">
                  <span className={`recent-status-dot ${statusClass(s.status)}`} title={s.status} />
                  <div className="recent-row-body">
                    <span className="recent-repo" title={s.repo_path}>
                      {repositoryName(s.repo_path)}
                    </span>
                    <span className="recent-meta">
                      {relativeTime(s.finished_at || s.started_at)}
                      {dur != null && <span>&middot; {dur}s</span>}
                      <span>&middot; {s.coverage_pct}% coverage</span>
                      {s.assets_found > 0 && <span>&middot; {s.assets_found} findings</span>}
                    </span>
                  </div>
                </Link>
                {canWrite() && (
                  <button
                    className="recent-rescan-btn"
                    disabled={scanningRepo === s.repo_path}
                    onClick={() => onRescan(s.repo_path)}
                    title="Re-run scan on this repository"
                  >
                    {scanningRepo === s.repo_path ? "Scanning…" : "Rescan"}
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}
    </motion.section>
  );
});

// ── Sub-row: Supporting Trends ──────────────────────────────────
const SupportingTrends = memo(function SupportingTrends({
  summary,
}: {
  summary: DashboardSummary;
}) {
  return (
    <motion.section className="dashboard-trends" variants={staggerItem}>
      <div className="trends-chart">
        <h2>Full-scan risk distribution</h2>
        <Suspense fallback={<div className="skeleton" style={{ height: 200 }} />}>
          <RiskDistributionChart
            data={["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((label) => ({
              label,
              count: summary.risk_distribution[label as keyof typeof summary.risk_distribution],
            }))}
          />
        </Suspense>
      </div>
      {summary.blind_spots.length > 0 && (
        <div className="trends-blind-spots">
          <h3>Visibility gaps</h3>
          <ul>
            {summary.blind_spots.slice(0, 4).map((gap, i) => (
              <li key={gap}>
                <span className="blind-spot-num">{i + 1}</span>
                <p>{gap}</p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </motion.section>
  );
});

// ── Main Dashboard Component ────────────────────────────────────
export default function Dashboard() {
  const { toast } = useToast();
  const [summary, setSummary] = useState<DashboardSummary>();
  const [evaluation, setEvaluation] = useState<Evaluation>();
  const [error, setError] = useState("");
  const [downloadError, setDownloadError] = useState("");
  const [sending, setSending] = useState("");
  const [searchParams, setSearchParams] = useSearchParams();
  const rawScanId = searchParams.get("scan_id");
  const scanId = rawScanId && /^\d+$/.test(rawScanId) ? Number(rawScanId) : undefined;
  const [scans, setScans] = useState<ScanJob[]>([]);
  const [attentionAssets, setAttentionAssets] = useState<CryptoAsset[]>([]);
  const [scanStatusFilter, setScanStatusFilter] = useState<ScanStatusFilter>(() => {
    const value = searchParams.get("scan_status") as ScanStatusFilter | null;
    return value && SCAN_STATUS_FILTERS.includes(value) ? value : "all";
  });
  const [scanSearch, setScanSearch] = useState("");
  const [scanningRepo, setScanningRepo] = useState("");

  useEffect(() => {
    let cancelled = false;
    setSummary(undefined);
    setEvaluation(undefined);
    setError("");
    setDownloadError("");
    Promise.all([getDashboardSummary(scanId), getEvaluation(scanId).catch(() => undefined)])
      .then(([s, e]) => {
        if (cancelled) return;
        setSummary(s);
        setEvaluation(e);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(String(e));
          toast("Failed to load dashboard: " + String(e), "error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [scanId, toast]);

  useEffect(() => {
    let cancelled = false;
    getScans()
      .then((r) => {
        if (!cancelled) setScans(r.slice(0, 8));
      })
      .catch(() => {});
    Promise.all([
      getAssets(scanId, { risk: "CRITICAL", limit: 10, sort: "priority" }),
      getAssets(scanId, { risk: "HIGH", limit: 10, sort: "priority" }),
      getAssets(scanId, { quantum: true, limit: 10, sort: "priority" }),
    ])
      .then(([critical, high, quantum]) => {
        if (cancelled) return;
        const seen = new Set<number>();
        const combined: CryptoAsset[] = [];
        for (const a of [...critical.items, ...high.items, ...quantum.items]) {
          if (!seen.has(a.id)) {
            seen.add(a.id);
            combined.push(a);
          }
        }
        combined.sort((a, b) => (b.priority_score ?? 0) - (a.priority_score ?? 0));
        setAttentionAssets(combined.slice(0, 10));
      })
      .catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [scanId]);

  const filteredScans = useMemo(() => {
    let result = scans;
    if (scanStatusFilter !== "all") {
      result = result.filter((s) => s.status === scanStatusFilter);
    }
    if (scanSearch.trim()) {
      const q = scanSearch.toLowerCase();
      result = result.filter((s) => s.repo_path.toLowerCase().includes(q));
    }
    return result;
  }, [scans, scanStatusFilter, scanSearch]);

  const handleRescan = useCallback(
    async (repoPath: string) => {
      if (!canWrite()) return;
      setScanningRepo(repoPath);
      try {
        await scanRepo(repoPath);
        getScans().then((r) => {
          if (!r) return;
          setScans(r.slice(0, 8));
        });
      } catch (e) {
        toast("Scan failed: " + String(e), "error");
      } finally {
        setScanningRepo("");
      }
    },
    [toast, setScans],
  );

  const clearScanFilter = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    next.delete("scan_id");
    setSearchParams(next, { replace: true });
  }, [searchParams, setSearchParams]);

  const changeScanStatusFilter = (value: ScanStatusFilter) => {
    setScanStatusFilter(value);
    const next = new URLSearchParams(searchParams);
    if (value === "all") next.delete("scan_status");
    else next.set("scan_status", value);
    setSearchParams(next, { replace: true });
  };

  if (error)
    return (
      <div className="state" role="alert">
        <span className="spinner" aria-hidden="true" />
        <h1>Dashboard unavailable</h1>
        <p>{error}</p>
      </div>
    );
  if (!summary)
    return (
      <div className="dashboard-page" role="status" aria-live="polite">
        <SkeletonCard />
        <div style={{ display: "flex", gap: 12 }}>
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
        <SkeletonTable rows={6} />
      </div>
    );
  if (!summary.latest_scan_id) {
    if (scanId) {
      return (
        <State
          title="Scan not found"
          body={"Scan #" + scanId + " was not found or has not completed yet."}
        />
      );
    }
    return <Empty />;
  }

  const scanQuery = scanId ? "?scan_id=" + scanId : "";

  return (
    <div className="dashboard-page">
      {scanId && (
        <div className="scan-filter-banner">
          <span>Viewing results from scan #{scanId}</span>
          <button
            className="button secondary"
            onClick={clearScanFilter}
            style={{ padding: "4px 12px", fontSize: 12 }}
          >
            Show latest
          </button>
        </div>
      )}
      <motion.section
        className="hero dashboard-hero"
        initial={{ opacity: 0, y: 18 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: [0.25, 0.1, 0.25, 1] }}
      >
        <div>
          <p className="eyebrow">Enterprise posture</p>
          <h1>Cryptographic assurance overview</h1>
          <p>
            Measured discovery coverage, evidence strength, quantum exposure, and migration
            priority.
          </p>
          {scans[0]?.started_at && (
            <p className="hero-meta">Last scan {relativeTime(scans[0].started_at)}</p>
          )}
        </div>
        <div className="hero-actions">
          <button
            className="button secondary"
            disabled={sending !== ""}
            onClick={() => {
              setDownloadError("");
              setSending("risk");
              downloadReport(
                "/api/reports/risk.txt" + scanQuery,
                scanId ? "ecdat-risk-report-scan-" + scanId + ".txt" : "ecdat-risk-report.txt",
              )
                .catch((e) => setDownloadError(String(e)))
                .finally(() => setSending(""));
            }}
          >
            {sending === "risk" ? "Preparing…" : "Download risk report"}
          </button>
          <Link className="button" to={"/cbom" + scanQuery}>
            View CBOM
          </Link>
        </div>
      </motion.section>
      <nav className="judge-path" aria-label="Judge demo path">
        <span className="judge-path-label">Demo path</span>
        <Link to="/scan" aria-label="1 Start scan">
          <b>1</b>
          <span>
            Start scan<small>Run discovery once</small>
          </span>
        </Link>
        <Link to="/scans" aria-label="2 Verify history">
          <b>2</b>
          <span>
            Verify history<small>Inspect job evidence</small>
          </span>
        </Link>
        <Link to={"/assets" + scanQuery} aria-label="3 Inspect findings">
          <b>3</b>
          <span>
            Inspect findings<small>Review source context</small>
          </span>
        </Link>
        <Link to={"/cbom" + scanQuery} aria-label="4 Export CBOM">
          <b>4</b>
          <span>
            Export CBOM<small>Deliver evidence</small>
          </span>
        </Link>
      </nav>
      {(downloadError || error) && (
        <div className="callout error" role="alert">
          {downloadError}
        </div>
      )}
      <motion.section
        className="dashboard-rows"
        variants={staggerContainer}
        initial="initial"
        animate="animate"
      >
        <PostureRow summary={summary} scans={scans} />
        <AssuranceQuality summary={summary} evaluation={evaluation} />
        <SupportingTrends summary={summary} />
        <NeedsAttention assets={attentionAssets} scanId={scanId} />
        <UrgentActions summary={summary} />
        <RecentScans
          scans={filteredScans}
          scanStatusFilter={scanStatusFilter}
          onStatusFilterChange={changeScanStatusFilter}
          scanSearch={scanSearch}
          onScanSearchChange={setScanSearch}
          onRescan={handleRescan}
          scanningRepo={scanningRepo}
        />
      </motion.section>
    </div>
  );
}

function State({ title, body }: { title: string; body: string }) {
  return (
    <div className="state">
      <span className="spinner" />
      <h1>{title}</h1>
      <p>{body}</p>
    </div>
  );
}

function Empty() {
  return (
    <div className="empty-hero">
      <span className="radar">&#9672;</span>
      <p className="eyebrow">No completed inventory</p>
      <h1>Start with evidence, not assumptions.</h1>
      <p>
        Scan the bundled enterprise repository to build a measured cryptographic inventory and PQC
        migration view.
      </p>
      {canWrite() ? (
        <Link className="button" to="/scan">
          Run discovery scan
        </Link>
      ) : (
        <p className="muted" style={{ marginTop: 12 }}>
          Ask an administrator or security analyst to run a scan.
        </p>
      )}
    </div>
  );
}
