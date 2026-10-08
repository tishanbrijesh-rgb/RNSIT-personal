# ECDAT-ImpactX — Comprehensive Implementation Plan

> Last updated: 2026-09-12
> Status: Scanning bug fixed and verified. Frontend tests pass (23/23). TypeScript compiles clean.

---

## Status of Previous Work (From Earlier Session)

### Completed
| Item | Description |
|------|-------------|
| **P0 Bug: Status mismatch** | `"complete"` vs `"completed"` in ScanPage.tsx and Dashboard.tsx — **FIXED** |
| **P1: Subprocess stderr capture** | scan_control.py now captures worker stderr to temp file and logs it — **FIXED** |
| **TypeScript clean** | All files compile with zero errors |
| **Frontend tests pass** | 23/23 tests green |

---

## Remaining Frontend Issues (F1–F5)

### F1 — Global Shell / Layout Issues
| # | Issue | Severity | Notes |
|---|-------|----------|-------|
| 1.1 | Missing `<title>` tag in `index.html` | Low | Should reflect current page |
| 1.2 | No favicon configured | Low | |
| 1.3 | Navbar doesn't collapse on mobile | Medium | Hamburger menu missing |
| 1.4 | No skip-to-content link for a11y | Low | |

### F2 — Dashboard Attention / Empty States
| # | Issue | Severity | Notes |
|---|-------|----------|-------|
| 2.1 | `NeedsAttention` takes raw `assets` prop instead of computing from summary | Medium | Dashboard passes `criticalAssets` but component is called "NeedsAttention" — misleading. Should show all CRITICAL+HIGH+conflict+quantum assets from the scan query |
| 2.2 | Empty state "No completedd inventory" — typo "completedd" | Low | Line 698 of Dashboard.tsx |
| 2.3 | No data staleness indicator | Low | Show "last updated" timestamp |
| 2.4 | `criticalAssets` fetched with fixed `risk=CRITICAL` — should respect current `scanId` query | Medium | If user switches scan_id, critical assets don't refresh |

### F3 — Mobile Inventory (Assets Page)
| # | Issue | Severity | Notes |
|---|-------|----------|-------|
| 3.1 | Table doesn't collapse to cards on mobile | High | Current `table-wrap` has no responsive breakpoint |
| 3.2 | No column visibility toggles | Low | |
| 3.3 | Pagination not visible in table | Medium | Check if pagination component exists |

### F4 — Scan Page Polish
| # | Issue | Severity | Notes |
|---|-------|----------|-------|
| 4.1 | `blindSpots` state never updated from SSE events | Medium | Line 37 declares `blindSpots` but the event handler never populates it |
| 4.2 | Advanced options (max depth, min size) are not sent to backend | Medium | They render inputs but the `handleStart` only passes `repoPath` |
| 4.3 | No confirmation before starting a long scan | Low | |
| 4.4 | Recent paths chips don't truncate long paths | Low | |

### F5 — Reports / CBOM
| # | Issue | Severity | Notes |
|---|-------|----------|-------|
| 5.1 | RiskReport page uses `lazy` but no fallback shown | Low | Already Suspense-wrapped |
| 5.2 | CBOM download doesn't pass scan_id | Medium | Check CBOM export endpoint |
| 5.3 | No print styles for reports | Low | |

---

## Backend Improvements (Phase 1–6)

### Phase 1 — Test Suite Foundation
| # | Task | Priority |
|---|------|----------|
| 1.1 | Add `tests/` directory with `conftest.py` and fixtures | High |
| 1.2 | Write scan endpoint tests (create, cancel, status, events) | High |
| 1.3 | Write asset/report endpoint tests | Medium |

### Phase 2 — Reliability
| # | Task | Priority |
|---|------|----------|
| 2.1 | Retry with backoff for transient DB errors (SQLite lock, connection drops) | High |
| 2.2 | Request timeout middleware — hard cap to prevent hanging workers | Medium |
| 2.3 | Validate all incoming request payloads with Pydantic | Medium |

### Phase 3 — Observability
| # | Task | Priority |
|---|------|----------|
| 3.1 | Structured logging with request-correlation ID | Medium |
| 3.2 | Scan duration percentile tracking (p50, p95, p99) | Low |
| 3.3 | Prometheus `/metrics` endpoint | Low |

### Phase 4 — Security Hardening
| # | Task | Priority |
|---|------|----------|
| 4.1 | Sanitize and whitelist repo_path to prevent traversal | High |
| 4.2 | CORS origin whitelist from config (not hardcoded) | Medium |
| 4.3 | Audit-log retention policy (auto-purge after 90 days) | Low |

### Phase 5 — Performance
| # | Task | Priority |
|---|------|----------|
| 5.1 | Pagination on `/api/assets` (server-side limit/offset) | High |
| 5.2 | DB indexes on `scan_job_id`, `priority_score`, `priority_label` | High |
| 5.3 | Cache `getDashboardSummary` (30s TTL) | Medium |

### Phase 6 — Documentation & DevOps
| # | Task | Priority |
|---|------|----------|
| 6.1 | OpenAPI/Swagger descriptions on all routes | Medium |
| 6.2 | `docker-compose.yml` for one-command startup | Medium |
| 6.3 | README with setup, scan flow, troubleshooting | High |

---

## Recommended Execution Order

```
Week 1 (Quick wins + critical fixes):
  F4.1  — blindSpots SSE binding
  F4.2  — advanced options → backend
  F2.1  — NeedsAttention component refactor
  F2.2  — typo fix
  Phase 1.1–1.2 — backend test suite

Week 2 (Mobile + performance):
  F3.1  — responsive asset table
  Phase 5.1–5.2 — backend pagination + indexes

Week 3 (Security + polish):
  Phase 4.1 — repo_path sanitization
  F5.2  — CBOM scan_id
  Phase 2.1 — retry logic

Week 4 (Docs + infrastructure):
  Phase 6.3 — README
  Phase 6.1 — API docs
  Phase 6.2 — Docker
  Remaining lower-priority items
```
