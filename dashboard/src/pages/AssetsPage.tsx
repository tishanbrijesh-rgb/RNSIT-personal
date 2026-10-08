// Searchable, filterable, paginated cryptographic inventory.
// Filter state is synced to URL search params for shareability.
import { useEffect, useRef, useState, useCallback, memo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { motion, AnimatePresence } from "framer-motion";
import { getAssets, getDashboardSummary, exportAssetsCsv, canWrite } from "../api/client";
import { RiskBadge } from "../components/RiskBadge";
import { displayPath, highlightText } from "../utils/format";
import type { CryptoAsset, DashboardSummary } from "../types";

const PAGE_SIZE_OPTIONS = [25, 50, 100];
const RISK_LABELS = ["CRITICAL", "HIGH", "MEDIUM", "LOW"] as const;
type RiskFilter = (typeof RISK_LABELS)[number] | "ALL";
type SortOption = "priority" | "confidence" | "algorithm";
type ColumnKey = "asset" | "context" | "evidence" | "assurance" | "priority" | "action";
type ViewMode = "comfortable" | "dense";

const ALL_COLUMNS: ColumnKey[] = [
  "asset",
  "context",
  "evidence",
  "assurance",
  "priority",
  "action",
];

// ── Confidence bar (inline mini) ───────────────────────────────
const ConfidenceMini = memo(function ConfidenceMini({ value }: { value: number }) {
  const pct = Math.round(value * 100);
  const tone = pct >= 80 ? "teal" : pct >= 50 ? "amber" : "red";
  return (
    <span className={`conf-mini conf-mini--${tone}`} title={`${pct}% evidence confidence`}>
      <span className="conf-mini-track" aria-hidden="true">
        <span className="conf-mini-fill" style={{ width: `${pct}%` }} />
      </span>
      <span className="conf-mini-label">{pct}%</span>
    </span>
  );
});

function readNonNegativeInt(value: string | null, fallback: number) {
  const parsed = Number(value);
  return Number.isInteger(parsed) && parsed >= 0 ? parsed : fallback;
}

function readColumnList(value: string | null): ColumnKey[] {
  if (!value) return ["asset", "context", "evidence", "priority", "action"];
  const parts = value
    .split(",")
    .filter((p): p is ColumnKey => ALL_COLUMNS.includes(p as ColumnKey));
  return parts.length > 0 ? parts : ["asset", "context", "evidence", "priority", "action"];
}

export default function AssetsPage() {
  const [params, setParams] = useSearchParams();
  const scanId = params.get("scan_id");
  const page = readNonNegativeInt(params.get("page"), 0);
  const requestedPageSize = readNonNegativeInt(params.get("page_size"), 50);
  const pageSize = PAGE_SIZE_OPTIONS.includes(requestedPageSize) ? requestedPageSize : 50;
  const mode = (params.get("mode") === "dense" ? "dense" : "comfortable") as ViewMode;
  const visibleCols = readColumnList(params.get("cols"));

  const [query, setQuery] = useState(() => params.get("q") || "");
  const [debouncedQuery, setDebouncedQuery] = useState(query);
  const [risk, setRisk] = useState<RiskFilter>(() => {
    const value = params.get("risk");
    return RISK_LABELS.includes(value as (typeof RISK_LABELS)[number])
      ? (value as RiskFilter)
      : "ALL";
  });
  const [quantum, setQuantum] = useState(() => params.get("quantum") === "1");
  const [sortBy, setSortBy] = useState<SortOption>(() => {
    const value = params.get("sort");
    return value === "confidence" || value === "algorithm" ? value : "priority";
  });
  const [selectedIds, setSelectedIds] = useState<Set<number>>(new Set());
  const [showColMenu, setShowColMenu] = useState(false);
  const colMenuRef = useRef<HTMLDivElement>(null);

  const syncParams = useCallback(
    (updates: Record<string, string | undefined>) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          for (const [k, v] of Object.entries(updates)) {
            if (v === undefined || v === "" || v === "ALL") next.delete(k);
            else next.set(k, v);
          }
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const filterSignature = JSON.stringify([query, risk, quantum, sortBy]);
  const filterRef = useRef(filterSignature);

  useEffect(() => {
    syncParams({
      q: query || undefined,
      risk: risk === "ALL" ? undefined : risk,
      quantum: quantum ? "1" : undefined,
      sort: sortBy === "priority" ? undefined : sortBy,
    });
  }, [query, risk, quantum, sortBy, syncParams]);

  useEffect(() => {
    if (filterRef.current !== filterSignature) {
      filterRef.current = filterSignature;
      syncParams({ page: undefined });
    }
  }, [filterSignature, syncParams]);

  useEffect(() => {
    const timer = window.setTimeout(() => setDebouncedQuery(query), 250);
    return () => window.clearTimeout(timer);
  }, [query]);

  // State declarations
  const [assets, setAssets] = useState<CryptoAsset[]>([]);
  const [total, setTotal] = useState(0);
  const [summary, setSummary] = useState<DashboardSummary>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [exportError, setExportError] = useState("");
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const effectiveScanId = scanId ? Number(scanId) : undefined;
    getDashboardSummary(effectiveScanId)
      .then((result) => {
        if (!cancelled) setSummary(result);
      })
      .catch(() => {
        if (!cancelled) setSummary(undefined);
      });
    return () => {
      cancelled = true;
    };
  }, [scanId]);

  // Fetch assets
  useEffect(() => {
    const effectiveScanId = scanId ? Number(scanId) : undefined;
    const controller = new AbortController();
    setLoading(true);
    setError("");
    getAssets(effectiveScanId, {
      limit: pageSize,
      offset: page * pageSize,
      query: debouncedQuery,
      risk: risk === "ALL" ? undefined : risk,
      quantum: quantum ? true : undefined,
      sort: sortBy,
      signal: controller.signal,
    })
      .then((result) => {
        const lastPage = Math.max(0, Math.ceil(result.total / pageSize) - 1);
        if (page > lastPage) {
          syncParams({ page: lastPage ? String(lastPage) : undefined });
          return;
        }
        setAssets(result.items);
        setTotal(result.total);
        setLoading(false);
      })
      .catch((e) => {
        if (controller.signal.aborted) return;
        setError(String(e));
        setLoading(false);
      });
    return () => controller.abort();
  }, [scanId, page, pageSize, debouncedQuery, risk, quantum, sortBy, syncParams]);

  // Outside-click handler for column menu
  useEffect(() => {
    if (!showColMenu) return;
    const handler = (e: MouseEvent) => {
      if (colMenuRef.current && !colMenuRef.current.contains(e.target as Node)) {
        setShowColMenu(false);
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [showColMenu]);

  // Bulk selection
  const toggleSelect = useCallback((id: number) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const toggleSelectAll = useCallback(() => {
    if (selectedIds.size === assets.length && assets.length > 0) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(assets.map((a) => a.id)));
    }
  }, [assets, selectedIds]);

  const clearSelection = useCallback(() => setSelectedIds(new Set()), []);

  const handleExport = async (mode: "all" | "selected") => {
    if (mode === "selected" && selectedIds.size === 0) return;
    setExporting(true);
    setExportError("");
    try {
      const effectiveScanId = scanId ? Number(scanId) : undefined;
      await exportAssetsCsv(effectiveScanId, {
        query: mode === "all" ? debouncedQuery : undefined,
        risk: mode === "all" && risk !== "ALL" ? risk : undefined,
        quantum: mode === "all" && quantum ? true : undefined,
        sort: sortBy,
        ids: mode === "selected" ? [...selectedIds] : undefined,
      });
    } catch (e) {
      setExportError(e instanceof Error ? e.message : "Asset export failed.");
    } finally {
      setExporting(false);
    }
  };

  const removeFilter = useCallback((type: "risk" | "quantum" | "query") => {
    if (type === "risk") setRisk("ALL");
    else if (type === "quantum") setQuantum(false);
    else if (type === "query") setQuery("");
  }, []);

  const hasFilters = risk !== "ALL" || quantum || query;
  const hasSelection = selectedIds.size > 0;

  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const goToPage = useCallback(
    (p: number) => {
      syncParams({ page: String(Math.max(0, p)) });
    },
    [syncParams],
  );

  const toggleCol = (col: ColumnKey) => {
    if (col === "asset" || col === "priority" || col === "action") return;
    const next = visibleCols.includes(col)
      ? visibleCols.filter((c) => c !== col)
      : [...visibleCols, col];
    syncParams({ cols: next.join(",") });
  };

  return (
    <>
      <section className="hero compact">
        <div>
          <p className="eyebrow">Standardized inventory</p>
          <h1>Cryptographic assets</h1>
          {!loading && (
            <p aria-live="polite" aria-atomic="true">
              {total > 0 ? `${total} finding${total === 1 ? "" : "s"} in scope` : "No findings yet"}
            </p>
          )}
        </div>
        <div className="hero-actions">
          {canWrite() && (
            <Link className="button" to="/scan">
              New scan
            </Link>
          )}
          <button
            className="button secondary"
            onClick={() => handleExport("all")}
            disabled={loading || total === 0 || exporting}
            title="Export all matching assets as CSV"
          >
            {exporting ? "Exporting..." : "Export CSV"}
          </button>
          <div className="view-mode-group" role="group" aria-label="View density">
            <button
              className={`view-mode-btn${mode === "comfortable" ? " active" : ""}`}
              onClick={() => syncParams({ mode: "comfortable" })}
              aria-pressed={mode === "comfortable"}
              title="Comfortable view"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <rect x="1" y="3" width="14" height="3" rx="1" fill="currentColor" opacity="0.3" />
                <rect x="1" y="8" width="14" height="3" rx="1" fill="currentColor" opacity="0.3" />
                <rect x="1" y="13" width="14" height="3" rx="1" fill="currentColor" opacity="0.3" />
              </svg>
            </button>
            <button
              className={`view-mode-btn${mode === "dense" ? " active" : ""}`}
              onClick={() => syncParams({ mode: "dense" })}
              aria-pressed={mode === "dense"}
              title="Dense view"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                <rect x="1" y="1" width="14" height="3" rx="1" fill="currentColor" opacity="0.4" />
                <rect
                  x="1"
                  y="6"
                  width="14"
                  height="2.5"
                  rx="1"
                  fill="currentColor"
                  opacity="0.4"
                />
                <rect
                  x="1"
                  y="10"
                  width="14"
                  height="2.5"
                  rx="1"
                  fill="currentColor"
                  opacity="0.4"
                />
                <rect x="1" y="14" width="14" height="2" rx="1" fill="currentColor" opacity="0.4" />
              </svg>
            </button>
            <div className="col-menu-wrap" ref={colMenuRef}>
              <button
                className="view-mode-btn"
                onClick={() => setShowColMenu((v) => !v)}
                aria-expanded={showColMenu}
                aria-haspopup="true"
                title="Show/hide columns"
              >
                <svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true">
                  <rect
                    x="1"
                    y="1"
                    width="6"
                    height="14"
                    rx="1"
                    fill="currentColor"
                    opacity="0.4"
                  />
                  <rect
                    x="9"
                    y="1"
                    width="6"
                    height="14"
                    rx="1"
                    fill="currentColor"
                    opacity="0.4"
                  />
                </svg>
              </button>
              <AnimatePresence>
                {showColMenu && (
                  <motion.div
                    className="col-menu"
                    role="menu"
                    initial={{ opacity: 0, y: -4 }}
                    animate={{ opacity: 1, y: 0 }}
                    exit={{ opacity: 0, y: -4 }}
                    transition={{ duration: 0.15 }}
                  >
                    {ALL_COLUMNS.filter(
                      (c) => c !== "asset" && c !== "priority" && c !== "action",
                    ).map((col) => (
                      <label key={col} className="col-menu-item" role="menuitemcheckbox">
                        <input
                          type="checkbox"
                          checked={visibleCols.includes(col)}
                          onChange={() => toggleCol(col)}
                        />
                        <span className="col-menu-label">{col}</span>
                      </label>
                    ))}
                  </motion.div>
                )}
              </AnimatePresence>
            </div>
          </div>
        </div>
      </section>

      {/* ── Summary stats ──────────────────────────────────────── */}
      {summary && summary.total_assets > 0 && (
        <section className="assets-summary" aria-label="Asset summary">
          <div className="inventory-risk-summary" aria-label="Full scan risk summary">
            <div>
              <span className="posture-label">Full scan inventory</span>
              <strong className="inventory-total">{summary.total_assets.toLocaleString()}</strong>
              <small>Findings across the selected scan</small>
            </div>
            <div className="inventory-risk-grid">
              {RISK_LABELS.map((label) => {
                const count = summary.risk_distribution[label] || 0;
                return (
                  <span key={label} className={`asset-stat asset-stat--${label.toLowerCase()}`}>
                    <strong>{count.toLocaleString()}</strong> {label.toLowerCase()}
                  </span>
                );
              })}
            </div>
          </div>
          <ConfidenceHistogram counts={summary.confidence_distribution ?? {}} />
        </section>
      )}

      {/* ── Active filter chips ────────────────────────────────── */}
      {hasFilters && (
        <div className="filter-chips" role="list" aria-label="Active filters">
          {risk !== "ALL" && (
            <span className="filter-chip" role="listitem">
              <span className={`filter-chip-dot filter-chip-dot--${risk.toLowerCase()}`} />
              {risk}
              <button
                onClick={() => removeFilter("risk")}
                aria-label={`Remove ${risk} filter`}
                className="filter-chip-remove"
              >
                &times;
              </button>
            </span>
          )}
          {quantum && (
            <span className="filter-chip" role="listitem">
              <span className="filter-chip-dot filter-chip-dot--quantum" />
              Quantum vulnerable
              <button
                onClick={() => removeFilter("quantum")}
                aria-label="Remove quantum filter"
                className="filter-chip-remove"
              >
                &times;
              </button>
            </span>
          )}
          {query && (
            <span className="filter-chip" role="listitem">
              <span className="filter-chip-dot filter-chip-dot--search" />
              &ldquo;{query.length > 20 ? query.slice(0, 20) + "…" : query}&rdquo;
              <button
                onClick={() => removeFilter("query")}
                aria-label="Remove search filter"
                className="filter-chip-remove"
              >
                &times;
              </button>
            </span>
          )}
          <button
            className="filter-chip-clear"
            onClick={() => {
              setQuery("");
              setRisk("ALL");
              setQuantum(false);
            }}
          >
            Clear all
          </button>
        </div>
      )}

      {/* ── Bulk selection toolbar ────────────────────────────── */}
      <AnimatePresence>
        {hasSelection && (
          <motion.div
            className="bulk-bar"
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.15 }}
          >
            <span className="bulk-count">{selectedIds.size} selected</span>
            <button
              className="button secondary"
              onClick={() => handleExport("selected")}
              disabled={exporting}
              style={{ padding: "6px 12px", fontSize: 12 }}
            >
              Export selected
            </button>
            <button
              className="button secondary"
              onClick={clearSelection}
              style={{ padding: "6px 12px", fontSize: 12 }}
            >
              Clear selection
            </button>
          </motion.div>
        )}
      </AnimatePresence>

      {exportError && (
        <div className="callout error" role="alert">
          {exportError}
        </div>
      )}

      <section className="toolbar">
        <input
          aria-label="Search inventory"
          placeholder="Search algorithm, location, library…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <select
          value={risk}
          onChange={(e) => setRisk(e.target.value as RiskFilter)}
          aria-label="Filter by risk level"
        >
          <option value="ALL">All risks</option>
          {RISK_LABELS.map((r) => (
            <option key={r} value={r}>
              {r}
            </option>
          ))}
        </select>
        <label className="check" title="Show only quantum-vulnerable assets">
          <input type="checkbox" checked={quantum} onChange={(e) => setQuantum(e.target.checked)} />
          Quantum vulnerable
        </label>
        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value as SortOption)}
          aria-label="Sort assets"
          style={{ minWidth: 140 }}
        >
          <option value="priority">Sort: Priority</option>
          <option value="confidence">Sort: Evidence confidence</option>
          <option value="algorithm">Sort: Algorithm</option>
        </select>
      </section>
      {error && !loading && (
        <div className="callout error" role="alert">
          {error}
        </div>
      )}
      <div className="assets-mobile-list" aria-label="Cryptographic asset cards">
        {assets.map((asset) => (
          <AssetMobileCard
            key={asset.id}
            asset={asset}
            selected={selectedIds.has(asset.id)}
            onSelect={() => toggleSelect(asset.id)}
          />
        ))}
      </div>
      <div
        className={`panel table-wrap${mode === "dense" ? " dense" : ""}`}
        role="region"
        aria-label="Cryptographic assets table"
      >
        <table role="grid" aria-rowcount={Math.max(total, pageSize) + 1}>
          <thead>
            <tr>
              <th scope="col" style={{ width: 36 }}>
                <input
                  type="checkbox"
                  checked={assets.length > 0 && selectedIds.size === assets.length}
                  onChange={toggleSelectAll}
                  aria-label="Select all"
                  title="Select all on this page"
                />
              </th>
              <th scope="col">Asset</th>
              {visibleCols.includes("context") && <th scope="col">Context</th>}
              {visibleCols.includes("evidence") && <th scope="col">Evidence</th>}
              {visibleCols.includes("assurance") && <th scope="col">Assurance</th>}
              <th scope="col">Priority</th>
              {visibleCols.includes("action") && <th scope="col">Action</th>}
            </tr>
          </thead>
          <tbody>
            {loading
              ? Array.from({ length: pageSize }).map((_, i) => (
                  <tr key={`sk-${i}`} aria-rowindex={page * pageSize + i + 2}>
                    <td>
                      <div className="skeleton" style={{ height: 12, width: 12 }} />
                    </td>
                    {visibleCols.map((col, j) => (
                      <td key={col} aria-colindex={j + 2}>
                        <div className="skeleton" style={{ height: 12, width: "60%" }} />
                      </td>
                    ))}
                    <td>
                      <div className="skeleton" style={{ height: 12, width: "40%" }} />
                    </td>
                  </tr>
                ))
              : assets.map((a, rowIndex) => (
                  <tr
                    key={a.id}
                    aria-rowindex={page * pageSize + rowIndex + 2}
                    className={selectedIds.has(a.id) ? "row-selected" : ""}
                  >
                    <td aria-colindex={1}>
                      <input
                        type="checkbox"
                        checked={selectedIds.has(a.id)}
                        onChange={() => toggleSelect(a.id)}
                        aria-label={`Select ${a.algorithm} at ${a.location}`}
                      />
                    </td>
                    <td aria-colindex={2} data-label="Asset">
                      <Link to={`/assets/${a.id}`} className="asset-link">
                        <strong
                          dangerouslySetInnerHTML={{
                            __html: highlightText(
                              a.algorithm + (a.key_size ? `-${a.key_size}` : ""),
                              query,
                            ),
                          }}
                        />
                        <small>
                          {a.category} &middot;{" "}
                          <span
                            dangerouslySetInnerHTML={{ __html: highlightText(a.usage, query) }}
                          />
                        </small>
                      </Link>
                    </td>
                    {visibleCols.includes("context") && (
                      <td aria-colindex={3} data-label="Context">
                        <span
                          className="path"
                          title={a.location}
                          dangerouslySetInnerHTML={{
                            __html: highlightText(displayPath(a.location), query),
                          }}
                        />
                        <small>{a.library || a.protocol || "Direct source usage"}</small>
                      </td>
                    )}
                    {visibleCols.includes("evidence") && (
                      <td aria-colindex={4} data-label="Evidence">
                        <div className="source-row">
                          {a.source.map((s) => (
                            <span className={`source source-${s}`} key={s}>
                              {s}
                            </span>
                          ))}
                        </div>
                        <small>{a.evidence_json.evidence_list?.length || 0} records</small>
                      </td>
                    )}
                    {visibleCols.includes("assurance") && (
                      <td aria-colindex={5} data-label="Assurance">
                        <ConfidenceMini value={a.confidence} />
                        <small>{a.conflict ? "Review conflict" : "Evidence consistent"}</small>
                      </td>
                    )}
                    <td
                      aria-colindex={
                        visibleCols.includes("assurance") &&
                        visibleCols.includes("context") &&
                        visibleCols.includes("evidence")
                          ? 6
                          : visibleCols.includes("assurance")
                            ? 4
                            : 3
                      }
                      data-label="Priority"
                    >
                      <RiskBadge label={a.priority_label} score={a.priority_score} size="sm" />
                    </td>
                    {visibleCols.includes("action") && (
                      <td aria-colindex={ALL_COLUMNS.length + 1} data-label="Action">
                        <Link className="row-link" to={`/assets/${a.id}`}>
                          Inspect &rarr;
                        </Link>
                      </td>
                    )}
                  </tr>
                ))}
            {!loading && !assets.length && (
              <tr>
                <td colSpan={visibleCols.length + 2} className="empty-table-msg">
                  <span className="empty-data-icon">&#9632;</span>
                  <strong>
                    {hasFilters ? "No assets match these filters" : "No assets found"}
                  </strong>
                  <span>
                    {hasFilters
                      ? "Try adjusting your search or risk filter to see results."
                      : "Run a discovery scan to build your cryptographic inventory."}
                  </span>
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
      {!loading && total > 0 && (
        <PaginationControls
          page={page}
          totalPages={totalPages}
          pageSize={pageSize}
          total={total}
          onPageChange={goToPage}
          onPageSizeChange={(sz) => {
            syncParams({ page_size: String(sz), page: "0" });
          }}
          selectedCount={selectedIds.size}
        />
      )}
    </>
  );
}

function PaginationControls({
  page,
  totalPages,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  selectedCount,
}: {
  page: number;
  totalPages: number;
  pageSize: number;
  total: number;
  onPageChange: (p: number) => void;
  onPageSizeChange: (sz: number) => void;
  selectedCount: number;
}) {
  const start = total === 0 ? 0 : page * pageSize + 1;
  const end = Math.min((page + 1) * pageSize, total);

  return (
    <nav className="pagination-bar" aria-label="Pagination">
      <span className="muted">
        {start}&ndash;{end} of {total}
        {selectedCount > 0 && (
          <span className="pagination-selected"> &middot; {selectedCount} selected</span>
        )}
      </span>
      <select
        value={pageSize}
        onChange={(e) => onPageSizeChange(Number(e.target.value))}
        aria-label="Page size"
        className="page-size-select"
      >
        {PAGE_SIZE_OPTIONS.map((sz) => (
          <option value={sz} key={sz}>
            {sz} / page
          </option>
        ))}
      </select>
      <div className="pagination-buttons">
        <button
          className="button secondary"
          disabled={page <= 0}
          onClick={() => onPageChange(page - 1)}
        >
          &larr; Prev
        </button>
        <span>
          {page + 1} / {totalPages}
        </span>
        <button
          className="button secondary"
          disabled={page >= totalPages - 1}
          onClick={() => onPageChange(page + 1)}
        >
          Next &rarr;
        </button>
      </div>
    </nav>
  );
}

// ── Confidence Histogram ──────────────────────────────────────────
const CONFIDENCE_BINS = ["0-20", "21-40", "41-60", "61-80", "81-100"] as const;

const ConfidenceHistogram = memo(function ConfidenceHistogram({
  counts,
}: {
  counts: Record<string, number>;
}) {
  const bins = CONFIDENCE_BINS.map((bin) => Number(counts[bin] ?? 0));

  const maxCount = Math.max(...bins, 1);

  return (
    <div className="conf-histogram" aria-label="Evidence confidence distribution">
      <span className="conf-histogram-label">Evidence confidence</span>
      <div className="conf-histogram-bars">
        {CONFIDENCE_BINS.map((bin, i) => (
          <div key={bin} className="conf-histogram-col" aria-label={`${bin}%: ${bins[i]} assets`}>
            <div className="conf-histogram-plot">
              <div
                className={`conf-histogram-bar${bins[i] === 0 ? " conf-histogram-bar--zero" : ""}`}
                style={{ height: `${(bins[i] / maxCount) * 100}%` }}
                title={`${bin.replace("-", "–")}%: ${bins[i]} asset${bins[i] !== 1 ? "s" : ""}`}
              />
            </div>
            <span className="conf-histogram-count">{bins[i]}</span>
            <span className="conf-histogram-range">{bin}%</span>
          </div>
        ))}
      </div>
    </div>
  );
});

function AssetMobileCard({
  asset,
  selected,
  onSelect,
}: {
  asset: CryptoAsset;
  selected: boolean;
  onSelect: () => void;
}) {
  return (
    <article className={`asset-mobile-card${selected ? " selected" : ""}`}>
      <div className="asset-mobile-card-head">
        <label className="asset-mobile-select">
          <input type="checkbox" checked={selected} onChange={onSelect} />
          <span className="sr-only">Select {asset.algorithm}</span>
        </label>
        <div>
          <strong>
            {asset.algorithm}
            {asset.key_size ? `-${asset.key_size}` : ""}
          </strong>
          <span>
            {asset.category} · {asset.usage}
          </span>
        </div>
        <RiskBadge label={asset.priority_label} score={asset.priority_score} size="sm" />
      </div>
      <span className="path" title={asset.location}>
        {displayPath(asset.location)}
      </span>
      <div className="asset-mobile-evidence">
        <div className="source-row">
          {asset.source.map((source) => (
            <span className={`source source-${source}`} key={source}>
              {source}
            </span>
          ))}
        </div>
        <ConfidenceMini value={asset.confidence} />
      </div>
      <Link className="row-link asset-mobile-inspect" to={`/assets/${asset.id}`}>
        Inspect finding <span aria-hidden="true">→</span>
      </Link>
    </article>
  );
}
