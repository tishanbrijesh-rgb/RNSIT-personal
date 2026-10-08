// Scan detail — full job metrics, evidence summary, asset breakdown.
import { useState, useEffect, useMemo } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from "recharts";
import {
  getScanDetail,
  downloadCsv,
  subscribeScanEvents,
  getScans,
  scanRepo,
  canWrite,
} from "../api/client";
import { RiskBadge } from "../components/RiskBadge";
import { SkeletonPanel, SkeletonTable } from "../components/Skeletons";
import { useToast } from "../components/Toast";
import { formatDate } from "../utils/format";
import type { ScanDetail } from "../types";

export type AssetRiskFilter = "all" | "critical" | "high" | "conflict" | "quantum";

const SCAN_ASSET_FILTERS: {
  key: AssetRiskFilter;
  label: string;
}[] = [
  { key: "all", label: "All" },
  { key: "critical", label: "Critical" },
  { key: "high", label: "High" },
  { key: "conflict", label: "Conflicts" },
  { key: "quantum", label: "Quantum" },
];

function ScanAssetFilters({
  active,
  onChange,
  counts,
  total,
}: {
  active: AssetRiskFilter;
  onChange: (v: AssetRiskFilter) => void;
  counts: Record<string, number>;
  total: number;
}) {
  return (
    <div className="scan-asset-filters" role="group" aria-label="Filter scan assets">
      {SCAN_ASSET_FILTERS.map((f) => {
        const count =
          f.key === "all"
            ? total
            : f.key === "critical"
              ? counts.critical
              : f.key === "high"
                ? counts.high
                : f.key === "conflict"
                  ? counts.conflict
                  : counts.quantum;
        return (
          <button
            key={f.key}
            className={`scan-asset-filter-btn${active === f.key ? " active" : ""}`}
            onClick={() => onChange(f.key)}
            aria-pressed={active === f.key}
          >
            {f.label} ({count})
          </button>
        );
      })}
    </div>
  );
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

const STATUS_CLASS: Record<string, string> = {
  completed: "status-completed",
  failed: "status-failed",
  running: "status-running",
  cancelled: "status-cancelled",
  timed_out: "status-timed_out",
  queued: "status-running",
};

const FAILURE_LABELS: Record<string, string> = {
  unreadable: "Unreadable file",
  oversized: "File too large",
  linked_file: "Linked file",
  parse_error: "Parse error",
  certificate_error: "Certificate error",
};

export default function ScanDetailPage() {
  const navigate = useNavigate();
  const { id } = useParams();
  const { toast } = useToast();
  const [detail, setDetail] = useState<ScanDetail | null>(null);
  const [error, setError] = useState("");
  const [exporting, setExporting] = useState(false);
  const [rescanning, setRescanning] = useState(false);

  const [assetRiskFilter, setAssetRiskFilter] = useState<AssetRiskFilter>("all");
  const [previousScans, setPreviousScans] = useState<ScanDetail[]>([]);

  useEffect(() => {
    if (!id) return;
    const numericId = Number(id);
    getScans()
      .then((scans) => {
        const completed = scans
          .filter((s) => s.status === "completed" && s.id !== numericId)
          .sort((a, b) => b.id - a.id);
        if (completed.length > 0) {
          getScanDetail(completed[0].id).then((d) => setPreviousScans([d]));
        }
      })
      .catch(() => {});
  }, [id]);

  const prev = previousScans[0];

  const trends = useMemo(() => {
    if (!detail || !prev) return null;
    const prevAssets = prev.assets_found || 0;
    const prevConflicts = prev.summary.conflict_count;
    const prevQuantum = prev.summary.quantum_vulnerable_count;
    const prevCoverage = prev.coverage_pct || 0;
    const prevConfidence = prev.avg_confidence ?? 0;

    return {
      assets: detail.assets_found - prevAssets,
      coverage: detail.coverage_pct - prevCoverage,
      confidence: (detail.avg_confidence ?? 0) - prevConfidence,
      conflicts: detail.summary.conflict_count - prevConflicts,
      quantum: detail.summary.quantum_vulnerable_count - prevQuantum,
    };
  }, [detail, prev]);

  useEffect(() => {
    setDetail(null);
    setError("");
    if (!id || !/^\d+$/.test(id) || !Number.isSafeInteger(Number(id)) || Number(id) <= 0) {
      setError("Invalid scan ID.");
      return;
    }
    let cancelled = false;
    let unsubscribe: (() => void) | undefined;
    const numericId = Number(id);

    getScanDetail(numericId)
      .then((d) => {
        if (cancelled) return;
        setDetail(d);
        if (
          d.status === "completed" ||
          d.status === "failed" ||
          d.status === "cancelled" ||
          d.status === "timed_out"
        ) {
          return;
        }
        unsubscribe = subscribeScanEvents(
          numericId,
          (event) => {
            if (!cancelled) {
              setDetail((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  status: event.status,
                  collector_stats: event.collector_stats,
                  assets_found: event.assets_found,
                  coverage_pct: event.coverage_pct,
                  duration_ms: event.duration_ms,
                };
              });
            }
          },
          (final) => {
            if (!cancelled) {
              setDetail((prev) => {
                if (!prev) return prev;
                return {
                  ...prev,
                  status: final.status,
                  assets_found: final.assets_found,
                  coverage_pct: final.coverage_pct,
                  duration_ms: final.duration_ms,
                };
              });
            }
          },
          () => {
            if (!cancelled) setDetail((prev) => prev);
          },
        );
      })
      .catch((e) => {
        if (!cancelled) {
          setError(String(e));
          toast("Failed to load scan: " + String(e), "error");
        }
      });

    return () => {
      cancelled = true;
      unsubscribe?.();
      setAssetRiskFilter("all");
    };
  }, [id]);

  const riskCounts = detail?.summary.risk_distribution ?? {
    CRITICAL: 0,
    HIGH: 0,
    MEDIUM: 0,
    LOW: 0,
  };

  const hasMoreAssets = detail ? detail.assets_total > detail.assets.length : false;

  const assetRiskCounts = useMemo(() => {
    if (!detail) return { critical: 0, high: 0, conflict: 0, quantum: 0 };
    const counts: Record<string, number> = { critical: 0, high: 0, conflict: 0, quantum: 0 };
    detail.assets.forEach((a) => {
      if (a.priority_label === "CRITICAL") counts.critical++;
      if (a.priority_label === "HIGH") counts.high++;
      if (a.conflict) counts.conflict++;
      if (a.quantum_vulnerable) counts.quantum++;
    });
    return counts;
  }, [detail?.assets]);

  const filteredAssets = useMemo(() => {
    if (!detail || assetRiskFilter === "all") return detail?.assets ?? [];
    return (detail?.assets ?? []).filter((a) => {
      if (assetRiskFilter === "critical") return a.priority_label === "CRITICAL";
      if (assetRiskFilter === "high") return a.priority_label === "HIGH";
      if (assetRiskFilter === "conflict") return a.conflict;
      if (assetRiskFilter === "quantum") return a.quantum_vulnerable;
      return true;
    });
  }, [detail?.assets, assetRiskFilter]);

  const handleRescan = async () => {
    if (!detail || !canWrite()) return;
    setRescanning(true);
    try {
      const result = await scanRepo(detail.repo_path);
      toast("Rescan started", "success");
      navigate("/scans/" + result.scan_id);
    } catch (e) {
      toast("Rescan failed: " + String(e), "error");
      setRescanning(false);
    }
  };

  const handleExport = async () => {
    if (!detail) return;
    setExporting(true);
    try {
      const rows = detail.assets.map((a) => ({
        id: a.id,
        algorithm: a.algorithm,
        category: a.category,
        location: a.location,
        confidence: Math.round(a.confidence * 100),
        priority: a.priority_label,
        score: a.priority_score,
        quantum: a.quantum_vulnerable ? "Yes" : "No",
        sources: a.source.join("; "),
      }));
      const columns = [
        "id",
        "algorithm",
        "category",
        "location",
        "confidence",
        "priority",
        "score",
        "quantum",
        "sources",
      ];
      await downloadCsv(`scan-${detail.id}-assets.csv`, rows, columns);
      toast("CSV exported successfully", "success");
    } catch (e) {
      setError("Failed to export CSV.");
      toast("Export failed: " + String(e), "error");
    } finally {
      setExporting(false);
    }
  };

  if (error && !detail)
    return (
      <div className="state" role="alert">
        <span className="spinner" aria-hidden="true" />
        <h1>Scan detail unavailable</h1>
        <p>{error}</p>
      </div>
    );
  if (!detail) {
    return (
      <div className="scan-detail-page" role="status" aria-live="polite">
        <SkeletonPanel />
        <div style={{ display: "flex", gap: 12 }}>
          <SkeletonPanel />
          <SkeletonPanel />
          <SkeletonPanel />
          <SkeletonPanel />
          <SkeletonPanel />
          <SkeletonPanel />
        </div>
        <SkeletonTable rows={8} />
      </div>
    );
  }

  return (
    <div className="scan-detail-page">
      <section className="hero compact">
        <div>
          <p className="eyebrow">Scan detail</p>
          <h1>Scan #{detail.id}</h1>
          <p>
            {detail.repo_path} &middot;{" "}
            <span className={`status ${STATUS_CLASS[detail.status] || ""}`}>{detail.status}</span>{" "}
            &middot;
            {formatDuration(detail.duration_ms)}
          </p>
        </div>
        <div className="hero-actions">
          {detail.status === "completed" && (
            <button className="button secondary" disabled={exporting} onClick={handleExport}>
              {exporting ? "Exporting…" : "Export displayed CSV"}
            </button>
          )}
          {canWrite() && (
            <button className="button secondary" disabled={rescanning} onClick={handleRescan}>
              {rescanning ? "Rescanning…" : "Rescan"}
            </button>
          )}
          <Link className="button" to={`/assets?scan_id=${detail.id}`}>
            View all assets
          </Link>
        </div>
      </section>

      <section className="stats six">
        <div>
          <StatWithTrend
            label="Assets found"
            value={detail.assets_found}
            trend={trends?.assets}
            filter="all"
            scanId={id ? Number(id) : undefined}
          />
        </div>
        <div>
          <StatWithTrend label="Scanned" value={detail.scanned_files} tone="teal" />
        </div>
        <div>
          <StatWithTrend
            label="Supported-file coverage"
            value={`${detail.coverage_pct}%`}
            trend={trends?.coverage}
            suffix="%"
            tone="teal"
            scanId={id ? Number(id) : undefined}
          />
        </div>
        <div>
          <StatWithTrend
            label="Avg evidence confidence"
            value={`${detail.avg_confidence ? Math.round(detail.avg_confidence * 100) : 0}%`}
            tone={detail.avg_confidence != null && detail.avg_confidence >= 0.8 ? "teal" : "amber"}
          />
        </div>
        <div>
          <StatWithTrend
            label="Conflicts"
            value={detail.summary.conflict_count}
            trend={trends?.conflicts}
            tone="red"
            scanId={id ? Number(id) : undefined}
          />
        </div>
        <div>
          <StatWithTrend
            label="Quantum-vulnerable assets"
            value={detail.summary.quantum_vulnerable_count}
            trend={trends?.quantum}
            filter="quantum"
            tone="amber"
            scanId={id ? Number(id) : undefined}
          />
        </div>
      </section>

      <section className="scan-analytics-grid">
        <article className="panel scan-job-panel">
          <div className="panel-title">
            <h2>Job metrics</h2>
            <p>Scan execution details</p>
          </div>
          <div className="scan-detail-facts">
            <div className="scan-fact scan-fact--wide">
              <span className="scan-fact-label">Repository</span>
              <span className="scan-fact-value path">{detail.repo_path}</span>
            </div>
            <Fact label="Status" value={detail.status} />
            <Fact label="Duration" value={formatDuration(detail.duration_ms)} />
            <Fact label="Discovered" value={detail.total_files.toLocaleString()} />
            <Fact label="Eligible" value={detail.in_scope_files.toLocaleString()} />
            <Fact label="Successful" value={detail.scanned_files.toLocaleString()} />
            <Fact label="Failed" value={detail.failed_files.toLocaleString()} />
            {detail.started_at && <Fact label="Started" value={formatDate(detail.started_at)} />}
            {detail.finished_at && <Fact label="Finished" value={formatDate(detail.finished_at)} />}
          </div>
        </article>

        <article className="panel scan-coverage-panel">
          <div className="panel-title">
            <h2>Supported-file coverage</h2>
            <p>Successful files within the eligible scan scope</p>
          </div>
          <CoverageDonut
            scanned={detail.scanned_files}
            failed={detail.failed_files}
            percentage={detail.coverage_pct}
          />
          <div className="coverage-scope-note">
            <strong>{detail.in_scope_files.toLocaleString()}</strong> of{" "}
            <strong>{detail.total_files.toLocaleString()}</strong> discovered files were eligible.
            Coverage does not measure detection accuracy.
          </div>
        </article>

        <article className="panel scan-risk-panel">
          <div className="panel-title">
            <h2>Risk breakdown</h2>
            <p>All {detail.summary.total_assets.toLocaleString()} findings, not the preview</p>
          </div>
          <RiskDonut counts={riskCounts} />
        </article>

        <article className="panel scan-collector-panel">
          <div className="panel-title">
            <h2>Collector output</h2>
            <p>Independent evidence records</p>
          </div>
          <div className="collector-list scan-collector-list">
            {Object.entries(detail.collector_stats)
              .filter(([label, count]) => !label.startsWith("_") && typeof count === "number")
              .map(([label, count], _index, entries) => (
                <div key={label}>
                  <span>{label.toUpperCase()}</span>
                  <strong>{count}</strong>
                  <i
                    style={{
                      width: `${Math.min(100, (Number(count) / Math.max(1, ...entries.map(([, value]) => Number(value)))) * 100)}%`,
                    }}
                  />
                </div>
              ))}
          </div>
        </article>

        <article className="panel scan-span-full scan-assets-panel">
          <div className="panel-title">
            <h2>
              Assets ({filteredAssets.length}
              {hasMoreAssets ? ` of ${detail.assets_total}` : ""})
            </h2>
            <p>Cryptographic findings from this scan</p>
          </div>
          {hasMoreAssets && (
            <div className="callout callout-amber">
              This detail view is limited to the first 200 findings to stay responsive. Use the
              paginated <Link to={`/assets?scan_id=${detail.id}`}>inventory</Link> to inspect all{" "}
              {detail.assets_total} assets.
            </div>
          )}
          <ScanAssetFilters
            active={assetRiskFilter}
            onChange={setAssetRiskFilter}
            counts={assetRiskCounts}
            total={detail.assets.length}
          />
          {filteredAssets.length === 0 ? (
            <p className="muted">No assets match this filter.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Algorithm</th>
                    <th scope="col">Location</th>
                    <th scope="col">Evidence</th>
                    <th scope="col">Confidence</th>
                    <th scope="col">Priority</th>
                    <th scope="col" />
                  </tr>
                </thead>
                <tbody>
                  {filteredAssets.map((a) => (
                    <tr key={a.id}>
                      <td>
                        <strong>
                          {a.algorithm}
                          {a.key_size ? `-${a.key_size}` : ""}
                        </strong>
                        <small>
                          {a.category} &middot; {a.usage}
                        </small>
                      </td>
                      <td>
                        <span className="path">{a.location}</span>
                      </td>
                      <td>
                        <div className="source-row">
                          {a.source.map((s) => (
                            <span className={`source source-${s}`} key={s}>
                              {s}
                            </span>
                          ))}
                        </div>
                      </td>
                      <td>
                        <strong>{Math.round(a.confidence * 100)}%</strong>
                        <small>{a.conflict ? "Review conflict" : "Consistent"}</small>
                      </td>
                      <td>
                        <RiskBadge label={a.priority_label} score={a.priority_score} size="sm" />
                      </td>
                      <td>
                        <Link className="row-link" to={`/assets/${a.id}`}>
                          Inspect &rarr;
                        </Link>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </article>

        {detail.failures && detail.failures.length > 0 && (
          <article className="panel scan-span-full scan-failures">
            <div className="panel-title">
              <h2>Failed files</h2>
              <p>Safe relative paths and controlled failure categories</p>
            </div>
            <ul className="scan-failure-list">
              {detail.failures.map((failure) => (
                <li key={`${failure.path}:${failure.reason}`}>
                  <span className="path">{failure.path}</span>
                  <span className="status status-failed">
                    {FAILURE_LABELS[failure.reason] || "Processing error"}
                  </span>
                </li>
              ))}
            </ul>
          </article>
        )}

        {detail.blind_spots.length > 0 && (
          <article className="panel scan-span-full blind">
            <div className="panel-title">
              <h2>Blind spots</h2>
              <p>Areas outside this scan's measured scope</p>
            </div>
            <div className="gap-list">
              {detail.blind_spots.map((gap, i) => (
                <div key={gap}>
                  <span>{i + 1}</span>
                  <p>{gap}</p>
                </div>
              ))}
            </div>
          </article>
        )}
      </section>
    </div>
  );
}

function TrendArrow({ value }: { value: number }) {
  const isUp = value > 0;
  const cls = isUp ? "trend-up" : "trend-down";
  const arrow = isUp ? "▲" : "▼";
  return (
    <span
      className={`trend-arrow ${cls}`}
      aria-label={isUp ? "increased" : "decreased"}
      title={isUp ? `+${value}` : `${value}`}
    >
      {arrow}
    </span>
  );
}

function StatWithTrend({
  label,
  value,
  trend,
  tone,
  filter,
  suffix,
  scanId,
}: {
  label: string;
  value: number | string;
  trend?: number;
  tone?: string;
  filter?: string;
  suffix?: string;
  scanId?: number;
}) {
  const trendAbs = trend !== undefined ? Math.abs(trend) : undefined;

  const inner = (
    <>
      <span>{label}</span>
      <div className="stat-value-row">
        <strong>{typeof value === "number" && suffix === "%" ? `${value}%` : value}</strong>
        {trend !== undefined && trend !== 0 && <TrendArrow value={trend} />}
      </div>
      {trendAbs !== undefined && trendAbs > 0 && (
        <span className="stat-trend-detail">
          {trend! > 0 ? "+" : ""}
          {trend}
          {suffix || ""} vs prior scan
        </span>
      )}
    </>
  );

  if (filter) {
    return (
      <Link
        to={`/assets?scan_id=${scanId ?? ""}${filter === "quantum" ? "&quantum=1" : ""}`}
        className={`stat tone-${tone || "blue"} stat--clickable`}
      >
        {inner}
      </Link>
    );
  }

  return <article className={`stat tone-${tone || "blue"}`}>{inner}</article>;
}

function Fact({ label, value }: { label: string; value: string }) {
  return (
    <div className="scan-fact">
      <span className="scan-fact-label">{label}</span>
      <span className="scan-fact-value">{value}</span>
    </div>
  );
}

const RISK_META = [
  { label: "CRITICAL", color: "#b4232d" },
  { label: "HIGH", color: "#b96a00" },
  { label: "MEDIUM", color: "#d5a000" },
  { label: "LOW", color: "#07845f" },
] as const;

function RiskDonut({ counts }: { counts: Record<string, number> }) {
  const data = RISK_META.map((item) => ({ ...item, value: counts[item.label] || 0 }));
  const total = data.reduce((sum, item) => sum + item.value, 0);
  return (
    <div className="scan-donut-layout">
      <div
        className="scan-donut"
        role="img"
        aria-label={`Risk distribution across ${total} findings`}
      >
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="label"
              innerRadius="62%"
              outerRadius="88%"
              paddingAngle={total ? 2 : 0}
              stroke="none"
            >
              {data.map((item) => (
                <Cell key={item.label} fill={item.color} />
              ))}
            </Pie>
            <Tooltip formatter={(value: number) => value.toLocaleString()} />
          </PieChart>
        </ResponsiveContainer>
        <div className="scan-donut-center">
          <strong>{total.toLocaleString()}</strong>
          <span>findings</span>
        </div>
      </div>
      <div className="scan-chart-legend" aria-label="Risk distribution values">
        {data.map((item) => (
          <div key={item.label}>
            <span className="scan-chart-swatch" style={{ background: item.color }} />
            <span>{item.label}</span>
            <strong>{item.value.toLocaleString()}</strong>
            <small>{total ? `${Math.round((item.value / total) * 100)}%` : "0%"}</small>
          </div>
        ))}
      </div>
    </div>
  );
}

function CoverageDonut({
  scanned,
  failed,
  percentage,
}: {
  scanned: number;
  failed: number;
  percentage: number;
}) {
  const data = [
    { name: "Successful", value: scanned, color: "#07845f" },
    { name: "Failed", value: failed, color: "#b4232d" },
  ];
  return (
    <div className="scan-donut-layout scan-donut-layout--coverage">
      <div
        className="scan-donut"
        role="img"
        aria-label={`${percentage}% processing coverage: ${scanned} successful and ${failed} failed files`}
      >
        <ResponsiveContainer width="100%" height="100%">
          <PieChart>
            <Pie
              data={data}
              dataKey="value"
              nameKey="name"
              innerRadius="64%"
              outerRadius="88%"
              paddingAngle={2}
              stroke="none"
            >
              {data.map((item) => (
                <Cell key={item.name} fill={item.color} />
              ))}
            </Pie>
            <Tooltip formatter={(value: number) => value.toLocaleString()} />
          </PieChart>
        </ResponsiveContainer>
        <div className="scan-donut-center">
          <strong>{percentage}%</strong>
          <span>processed</span>
        </div>
      </div>
      <div className="scan-chart-legend">
        {data.map((item) => (
          <div key={item.name}>
            <span className="scan-chart-swatch" style={{ background: item.color }} />
            <span>{item.name}</span>
            <strong>{item.value.toLocaleString()}</strong>
          </div>
        ))}
      </div>
    </div>
  );
}
