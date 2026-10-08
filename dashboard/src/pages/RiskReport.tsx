// Structured risk report viewer — migration priorities, distribution, blind spots.
import { lazy, Suspense, useMemo, useState, useEffect, useCallback } from "react";
import { useSearchParams } from "react-router-dom";
import { downloadReport, getRiskReport, getEvaluation } from "../api/client";
import type { OutputPagination } from "../api/client";
import { RiskBadge } from "../components/RiskBadge";
import { SkeletonPanel, SkeletonTable } from "../components/Skeletons";
import { useToast } from "../components/Toast";
import { repositoryName } from "../utils/format";
import type { RiskLabel } from "../types";

const RiskDistributionChart = lazy(() => import("../components/RiskDistributionChart"));

const LABEL_ORDER: RiskLabel[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];
const PAGE_SIZE = 100;
const PRIORITY_FILTERS = [
  { key: "all", label: "All", match: (_label: RiskLabel) => true },
  { key: "critical", label: "Critical", match: (label: RiskLabel) => label === "CRITICAL" },
  { key: "high", label: "High", match: (label: RiskLabel) => label === "HIGH" },
  { key: "medium", label: "Medium", match: (label: RiskLabel) => label === "MEDIUM" },
  { key: "low", label: "Low", match: (label: RiskLabel) => label === "LOW" },
] as const;
type PriorityFilter = (typeof PRIORITY_FILTERS)[number]["key"];

export default function RiskReportPage() {
  const { toast } = useToast();
  const [params] = useSearchParams();
  const scanId = params.get("scan_id") ? Number(params.get("scan_id")) : undefined;

  const [report, setReport] = useState<
    (import("../types").RiskReport & { pagination?: OutputPagination }) | null
  >(null);
  const [evaluation, setEvaluation] = useState<import("../types").Evaluation | null>(null);
  const [error, setError] = useState("");
  const [filter, setFilter] = useState<PriorityFilter>("all");
  const [query, setQuery] = useState("");
  const [offset, setOffset] = useState(0);
  const [exporting, setExporting] = useState(false);
  const [exportedCount, setExportedCount] = useState(0);

  useEffect(() => {
    let cancelled = false;
    const risk = filter === "all" ? undefined : (filter.toUpperCase() as RiskLabel);
    Promise.all([
      getRiskReport(scanId, { limit: PAGE_SIZE, offset, risk, query }),
      getEvaluation(scanId).catch(() => null),
    ])
      .then(([r, ev]) => {
        if (cancelled) return;
        setReport(r);
        setEvaluation(ev);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(String(e));
          toast("Failed to load risk report: " + String(e), "error");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [scanId, filter, offset, query, toast]);

  const sorted = useMemo(
    () => [...(report?.migration_priorities ?? [])].sort((a, b) => b.score - a.score),
    [report?.migration_priorities],
  );

  const distribution = useMemo(() => {
    if (!report) return LABEL_ORDER.map((label) => ({ label, count: 0 }));
    const source = (report.summary?.risk_distribution ?? {}) as Record<string, number>;
    const values = LABEL_ORDER.map((label) => ({
      label,
      count: source[label] ?? 0,
    }));
    return values.some((item) => item.count > 0) ? values.filter((item) => item.count > 0) : values;
  }, [report]);

  const handleExport = useCallback(async () => {
    if (!report || exporting) return;
    setExporting(true);
    try {
      await downloadReport(
        `/api/reports/risk.csv${scanId ? `?scan_id=${scanId}` : ""}`,
        `ecdat-risk-report-${report.scan_id || "latest"}.csv`,
      );
      setExportedCount(report.pagination?.total ?? sorted.length);
      toast("Risk report exported as CSV", "success");
    } catch (e) {
      toast("Export failed: " + String(e), "error");
    } finally {
      setExporting(false);
    }
  }, [report, scanId, sorted.length, exporting, toast]);

  if (error && !report)
    return (
      <div className="state" role="alert">
        <span className="spinner" aria-hidden="true" />
        <h1>Risk report unavailable</h1>
        <p>{error}</p>
      </div>
    );
  if (!report) {
    return (
      <div className="dashboard-page" role="status" aria-live="polite">
        <SkeletonPanel />
        <SkeletonPanel />
        <SkeletonTable rows={8} />
      </div>
    );
  }

  const pagination = report.pagination ?? {
    total: sorted.length,
    filtered: sorted.length,
    offset: 0,
    limit: PAGE_SIZE,
    loaded: sorted.length,
  };
  const visiblePriorities = sorted;
  const riskCounts = (report.summary?.risk_distribution ?? {}) as Record<string, number>;
  const criticalCount = riskCounts.CRITICAL ?? 0;
  const highCount = riskCounts.HIGH ?? 0;
  const mediumCount = riskCounts.MEDIUM ?? 0;
  const lowCount = riskCounts.LOW ?? 0;
  const quantumCount = Number(report.summary?.quantum_vulnerable ?? 0);

  return (
    <>
      <section className="hero compact">
        <div>
          <p className="eyebrow">Migration roadmap</p>
          <h1>{report.title || "Risk report"}</h1>
          <p>
            <span title={report.repository}>{repositoryName(report.repository)}</span> &middot; Scan
            #{report.scan_id} &middot;
            {report.coverage_pct}% coverage
          </p>
        </div>
        <div className="hero-actions">
          <button
            className="button secondary"
            disabled={exporting || sorted.length === 0}
            onClick={handleExport}
          >
            {exporting ? "Exporting..." : "Export CSV"}
          </button>
        </div>
      </section>

      {/* Summary chips */}
      <section className="report-summary" aria-label="Report summary">
        <span className="report-chip report-chip--total">
          <strong>{pagination.total}</strong> asset{pagination.total !== 1 ? "s" : ""}
        </span>
        {criticalCount > 0 && (
          <span className="report-chip report-chip--critical">
            <strong>{criticalCount}</strong> critical
          </span>
        )}
        {highCount > 0 && (
          <span className="report-chip report-chip--high">
            <strong>{highCount}</strong> high
          </span>
        )}
        {mediumCount > 0 && (
          <span className="report-chip report-chip--medium">
            <strong>{mediumCount}</strong> medium
          </span>
        )}
        {lowCount > 0 && (
          <span className="report-chip report-chip--low">
            <strong>{lowCount}</strong> low
          </span>
        )}
        {quantumCount > 0 && (
          <span className="report-chip report-chip--quantum">
            <strong>{quantumCount}</strong> hybrid-ready
          </span>
        )}
        {evaluation?.available && (
          <span className="report-chip report-chip--eval">
            F1 {Math.round((evaluation.f1 || 0) * 100)}%
          </span>
        )}
      </section>

      <section className="dashboard-grid">
        <article className="panel span-full report-priorities-panel">
          <div className="panel-title">
            <h2>Risk distribution</h2>
            <p>Assets sorted by migration urgency</p>
          </div>
          <Suspense fallback={<div className="skeleton" style={{ height: 250 }} />}>
            <RiskDistributionChart data={distribution} />
          </Suspense>
        </article>

        <article className="panel">
          <div className="panel-title">
            <h2>Evaluation corpus</h2>
            {evaluation?.available && (
              <p>
                Corpus precision {Math.round((evaluation.precision ?? 0) * 100)}% &middot; corpus
                recall {Math.round((evaluation.recall ?? 0) * 100)}% &middot; F1{" "}
                {Math.round((evaluation.f1 ?? 0) * 100)}%
              </p>
            )}
          </div>
          {evaluation?.available ? (
            <div className="metric-grid">
              <MetricBlock
                label="Corpus precision"
                raw={
                  evaluation.precision != null ? `${Math.round(evaluation.precision * 100)}%` : "—"
                }
              />
              <MetricBlock
                label="Corpus recall"
                raw={evaluation.recall != null ? `${Math.round(evaluation.recall * 100)}%` : "—"}
              />
              <MetricBlock
                label="Corpus F1"
                raw={evaluation.f1 != null ? `${Math.round(evaluation.f1 * 100)}%` : "—"}
              />
              <MetricBlock label="Duration" raw={`${evaluation.duration_ms ?? 0} ms`} />
            </div>
          ) : (
            <div className="evaluation-unavailable" role="note">
              <strong>Precision is not available for this scan</strong>
              <p>
                ECDAT reports precision, recall, and F1 only when the repository contains a valid
                ground_truth.json. Supported-file coverage and evidence confidence are not
                substitutes for accuracy.
              </p>
            </div>
          )}
        </article>

        <article className="panel span-2">
          <div className="panel-title">
            <h2>Migration priorities</h2>
            <p>
              Total {pagination.total.toLocaleString()} · Loaded{" "}
              {pagination.loaded.toLocaleString()} · Filtered {pagination.filtered.toLocaleString()}{" "}
              · Exported {exportedCount.toLocaleString()}
            </p>
          </div>
          <div className="priority-filters" role="group" aria-label="Filter priorities">
            {PRIORITY_FILTERS.map((f) => (
              <button
                key={f.key}
                className={`priority-filter-btn${filter === f.key ? " active" : ""}`}
                onClick={() => {
                  setFilter(f.key);
                  setOffset(0);
                }}
                aria-pressed={filter === f.key}
              >
                {f.label}
              </button>
            ))}
          </div>
          <input
            aria-label="Search migration priorities"
            placeholder="Search algorithm, location, category, or usage"
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setOffset(0);
            }}
          />
          {visiblePriorities.length === 0 ? (
            <p className="muted">No assets match this filter.</p>
          ) : (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th scope="col">Algorithm</th>
                    <th scope="col">Location</th>
                    <th scope="col">Score</th>
                    <th scope="col">Level</th>
                    <th scope="col">Hybrid</th>
                    <th scope="col">Recommendation</th>
                  </tr>
                </thead>
                <tbody>
                  {visiblePriorities.map((p) => (
                    <tr key={p.asset_id}>
                      <td>
                        <strong>{p.algorithm}</strong>
                        <small>Asset #{p.asset_id}</small>
                      </td>
                      <td>
                        <span className="path">{p.location}</span>
                      </td>
                      <td>
                        <strong>{p.score}</strong>
                      </td>
                      <td>
                        <RiskBadge label={p.label} />
                      </td>
                      <td>{p.hybrid ? "Yes" : "No"}</td>
                      <td>{p.recommendation}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {pagination.filtered > pagination.limit && (
            <nav aria-label="Migration priority pages">
              <button
                className="button secondary"
                disabled={pagination.offset === 0}
                onClick={() => setOffset(Math.max(0, pagination.offset - pagination.limit))}
              >
                Previous
              </button>
              <button
                className="button secondary"
                disabled={pagination.offset + pagination.loaded >= pagination.filtered}
                onClick={() => setOffset(pagination.offset + pagination.limit)}
              >
                Next
              </button>
            </nav>
          )}
        </article>

        <article className="panel span-full blind">
          <div className="panel-title">
            <h2>Blind spots</h2>
            <p>Areas outside the scanner's measured scope</p>
          </div>
          <div className="gap-list">
            {(report.blind_spots ?? []).map((gap, i) => (
              <div key={gap}>
                <span>{i + 1}</span>
                <p>{gap}</p>
              </div>
            ))}
            {!report.blind_spots?.length && <p className="muted">No blind spots detected.</p>}
          </div>
        </article>
      </section>
    </>
  );
}

function MetricBlock({ label, raw }: { label: string; raw: string }) {
  return (
    <div className="metric-block">
      <span>{label}</span>
      <strong>{raw}</strong>
    </div>
  );
}
