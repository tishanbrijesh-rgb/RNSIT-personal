# ECDAT Frontend Improvement Plan

> **Last updated:** 2026-09-12 (original plan); 2026-09-22 (status audit added)
> **Current frontend status:** Dashboard React 19 + TypeScript app, 9 page components, 18 CSS files, 9 test files (23 tests passing). Vite build, React Router v7, Framer Motion animations, Lucide icons.

---

## Current Codebase Status (2026-09-22)

This section maps each phase from the original plan to what is already implemented, partially done, or still pending.

### Phase F1 — Global Shell (P0)

| Item | Original Plan | Current Status | Notes |
|------|--------------|----------------|-------|
| Compact logo + subtitle | Planned | Done | Brand mark in `pages.css:59-69`, "ASSURANCE" subtitle in `App.tsx` nav |
| Grouped nav + primary action | Planned | Done | 6 nav links in topbar, "New scan" has distinct `button primary` styling |
| Account menu + theme toggle | Planned | Done | `ThemeToggle.tsx` + Account dropdown in topbar |
| Responsive nav / hamburger | Planned | Partial | `responsive.css:75-98` wraps nav at 768px but no hamburger toggle — nav links shrink, not collapse |
| Consistent content width + rhythm | Planned | Done | `pages.css` topbar padding, consistent `--sp-*` tokens |
| Skip-to-content link | Planned | Missing | No skip link in `App.tsx` |

### Phase F2 — Overview (P0)

| Item | Original Plan | Current Status | Notes |
|------|--------------|----------------|-------|
| Assurance summary (status, ops, coverage) | Planned | Done | `PostureRow` in `Dashboard.tsx:96-200` shows posture, risk bar, chips |
| Needs attention section | Planned | Done | `NeedsAttention` component `Dashboard.tsx:362-413` |
| Risk distribution visual | Planned | Done | `RiskDistributionChart.tsx` (lazy-loaded D3 chart) |
| Recent scans timeline | Planned | Done | `RecentScans` in `Dashboard.tsx:416-511` with status filters |
| Empty state "Run your first scan" | Planned | Done | `Empty()` in `Dashboard.tsx:795-816` |
| API failure recovery panel | Planned | Missing | `State` component shows spinner only, no retry button |

### Phase F3 — Inventory (P0)

| Item | Original Plan | Current Status | Notes |
|------|--------------|----------------|-------|
| Readable table | Planned | Done | Full table with columns, sort, pagination in `AssetsPage.tsx` |
| Unified toolbar (search, risk, sort) | Planned | Done | `toolbar` section in `AssetsPage.tsx:545-578` |
| Removable filter chips | Planned | Done | `filter-chips` in `AssetsPage.tsx:462-514` |
| Human column names | Planned | Done | "Asset", "Context", "Evidence", "Assurance", "Priority", "Action" |
| Confirmed vs capability distinction | Planned | Done | "Capability only" badge in `AssetDetail.tsx:120-127` |
| Pagination / virtualization | Planned | Partial | Pagination exists (`PaginationControls` in `AssetsPage.tsx:756-817`) but no virtualization — fetches up to 10K assets at once |
| Mobile card layout | Planned | Done | `AssetMobileCard` in `AssetsPage.tsx:858-903`, responsive CSS at `responsive.css:135-184` |
| Mutually exclusive states (error/loading/empty/filtered) | Planned | Partial | Loading and error states exist, but error + empty can overlap |

### Phase F4 — New Scan (P0)

| Item | Original Plan | Current Status | Notes |
|------|--------------|----------------|-------|
| Three-step task flow | Planned | Partial | Single-step form in `ScanPage.tsx` — no step indicator |
| Inline validation | Planned | Done | `handlePathChange` validates absolute paths |
| Live progress (phase, files, findings, blind spots) | Planned | Done | SSE-driven progress in `ScanPage.tsx:121-146` |
| Completion summary with actions | Planned | Done | `scan-completed` view in `ScanPage.tsx:218-275` |
| Recent scans below form | Planned | Done | `scans` list fetched in `ScanPage.tsx:72-76` |

### Phase F5 — Reports & CBOM (P1)

| Item | Original Plan | Current Status | Notes |
|------|--------------|----------------|-------|
| Report header + scan selector | Planned | Done | `RiskReport.tsx` has scan selector, coverage, exports |
| Ranked migration priorities | Planned | Done | `migration-priorities` table with filters in `RiskReport.tsx` |
| Evaluation metrics section | Planned | Done | "Evaluation corpus" section in `RiskReport.tsx` |
| CBOM tabs (Components / Graph / JSON) | Planned | Done | Tabbed CBOM in `CbomPage.tsx` |
| Export buttons near title | Planned | Done | Export CSV + JSON buttons in `CbomPage.tsx` |

### Phase F6 — Design System (P1)

| Item | Original Plan | Current Status | Notes |
|------|--------------|----------------|-------|
| Consolidated CSS | Planned | Partial | 18 flat CSS files with implicit load order — functional but fragile |
| Standardized components | Planned | Done | `RiskBadge`, `ConfidenceBar`, `ConfidenceMini`, `StatusBadge`, `MetricCard` |
| One icon family | Planned | Partial | Mix of inline SVGs and Lucide imports across components |
| Motion limited to transform/opacity | Planned | Done | Framer Motion uses only opacity + y transforms |

### Phase F7 — A11y, Responsive, Performance (P1)

| Item | Original Plan | Current Status | Notes |
|------|--------------|----------------|-------|
| Keyboard order + skip link | Planned | Missing | No skip link, no formal keyboard order audit |
| Axe checks | Planned | Missing | No automated a11y testing in CI |
| 320–1920px validation | Planned | Partial | Breakpoints at 480, 640, 768, 820, 1050, 1441 — 320px untested |
| Visual regression | Planned | Missing | No screenshot-based testing |
| Skeleton loading | Planned | Partial | `Skeletons.tsx` exists but only used in `AssetsPage.tsx` |
| Lazy-load charts + CBOM | Planned | Done | `RiskDistributionChart` is lazy-loaded |

### Quick Wins

| Item | Status | File:Line |
|------|--------|-----------|
| Typo "completedd" → "completed" | Missing | `Dashboard.tsx:698` |
| Dynamic `<title>` per page | Missing | `index.html` / `main.tsx` |
| Theme persistence in `localStorage` | Missing | `ThemeToggle.tsx` |
| Inline SVGs → Lucide icons | Partial | Mixed across components |

---

## Implementation Tasks (File-Level)

### Sprint 1: Performance & Architecture (Week 1–2)

| Task | Files to Modify | Description |
|------|-----------------|-------------|
| T1.1 Code-split all pages | `App.tsx`, all `pages/*.tsx` | Wrap each page in `React.lazy()` + `<Suspense>`. `RiskDistributionChart` already lazy — follow same pattern. |
| T1.2 Virtualize assets table | `AssetsPage.tsx` | Add `@tanstack/react-virtual`. Replace row mapping with virtualizer. Target <50 DOM nodes at any page size. |
| T1.3 Extract `<AppShell>` | `App.tsx`, `pages/*.tsx` | Move topbar/nav/footer into `<AppShell>` with `<Outlet />`. Pages become pure content. |
| T1.4 Resource hints | `index.html` | Add `<link rel="preconnect">` for fonts, `<link rel="preload">` for critical CSS. |
| T1.5 Consolidated CSS | `src/styles/*.css` → `src/styles/tokens/`, `base/`, `components/`, `pages/` | Reorganize 18 files into a logical hierarchy. Add `index.css` `@import` chain. |

### Sprint 2: UX Polish (Week 3)

| Task | Files to Modify | Description |
|------|-----------------|-------------|
| T2.1 Toast system | `components/Toast.tsx`, `App.tsx`, all `pages/*.tsx` | Wire existing `Toast` to a context. Replace all inline `callout error` divs with toast triggers. |
| T2.2 Cmd+K global search | New `components/CommandPalette.tsx`, `App.tsx` | Modal search filtering assets by algorithm/location. Uses existing `getAssets` API. |
| T2.3 Skeleton states everywhere | `components/Skeletons.tsx`, `Dashboard.tsx`, `ScanDetailPage.tsx`, `RiskReport.tsx` | Replace `State` spinner fallbacks with layout-matched skeletons. |
| T2.4 Theme persistence | `components/ThemeToggle.tsx` | Read/write theme to `localStorage`. Respect `prefers-color-scheme` on first visit. |
| T2.5 Persist Dashboard filters in URL | `Dashboard.tsx` | Sync `scanStatusFilter` + `scanSearch` to `?status=` + `?q=` params. |

### Sprint 3: Accessibility & Mobile (Week 4)

| Task | Files to Modify | Description |
|------|-----------------|-------------|
| T3.1 Skip-to-content + focus styles | `App.tsx`, `styles/components.css` | Add skip link as first focusable element. Verify visible focus rings. |
| T3.2 Mobile hamburger nav | `App.tsx`, `styles/responsive.css` | Add hamburger toggle at `<768px`. Animate nav open/close. |
| T3.3 Focus trap in ConfirmDialog | `components/ConfirmDialog.tsx` | Add `focus-trap` behavior. Tab cycles within dialog, Escape closes. |
| T3.4 ARIA live regions | `ScanPage.tsx`, `AssetsPage.tsx`, `Dashboard.tsx` | Add `role="status"` / `role="alert"` for scan progress, filter changes, asset counts. |
| T3.5 Color contrast audit | All CSS files | Run axe DevTools. Fix any text failing 4.5:1 on dark backgrounds. |

### Sprint 4: New Features (Week 5–6)

| Task | Files to Modify | Description |
|------|-----------------|-------------|
| T4.1 Evidence graph full page | `pages/EvidenceGraphPage.tsx`, `components/EvidenceGraph.tsx` | Replace stub with interactive D3 force-directed graph. Click node → asset detail. |
| T4.2 Real-time scan progress in navbar | `App.tsx`, `components/Toast.tsx`, new `hooks/useScanProgress.ts` | Global SSE event bus. Topbar shows active scan progress bar. |
| T4.3 Bulk risk context editing | `AssetsPage.tsx` | Select multiple assets, batch-update risk fields via `updateAsset` PATCH. |
| T4.4 Scan comparison view | New `pages/ScanCompare.tsx`, `api/client.ts` | Side-by-side diff of two scans. New/removed/changed assets. |

### Sprint 5: Testing & QA (Week 7)

| Task | Files to Modify | Description |
|------|-----------------|-------------|
| T5.1 E2E smoke tests | New `e2e/` directory, `playwright.config.ts` | Login → scan → view assets → inspect → export CSV flow. |
| T5.2 Component test expansion | All `*.test.tsx` files | Add tests for ScanPage (SSE mock), ScanHistoryPage (filtering), EvidenceGraphPage, ConfirmDialog. |
| T5.3 Visual regression | Playwright screenshots | Baseline screenshots for Dashboard, Assets table, Asset detail states. |

---

## Definition of Done

- All top-level routes have polished loading, populated, empty, and error states.
- No raw API error string is the only content on a page.
- No broken counters, joined labels, clipped controls, or contradictory states.
- Primary tasks are usable with keyboard and at 375px.
- UI tests, E2E workflows, authenticated accessibility checks, build, lint, formatting, and performance budgets pass.
- Five people unfamiliar with ECDAT can start a scan and identify the highest-priority finding without coaching.

---

## Delivery Sequence

1. Global shell and design-system cleanup (Sprint 1).
2. UX polish: toasts, skeletons, search (Sprint 2).
3. Accessibility and mobile improvements (Sprint 3).
4. New features: evidence graph, scan comparison (Sprint 4).
5. Testing and QA gates (Sprint 5).
6. Continue with the backend/scanner roadmap in `implementation-improvement-plan-2026-09-12.md`.

## Product direction

ECDAT should feel like a focused security tool made for people who need to answer three questions quickly:

1. What cryptography do we have?
2. What needs attention first?
3. What should I do next?

The interface should be calm, direct, and practical. Keep the existing warm neutral palette and forest-green accent, but remove unfinished visual fragments, oversized empty areas, technical language that does not help decisions, and repeated card decoration. The result should feel authored by a security product team rather than assembled from dashboard templates.

## Design principles

- Prefer plain labels: “Overview”, “Inventory”, “New scan”, “Reports”, and “CBOM” are good; explain specialist terms where they first appear.
- Put the most useful action beside the information it affects.
- Use one primary action per view and make secondary actions visually quieter.
- Use cards only for true groups; use spacing, headings, dividers, and tables for the rest.
- Use real scan data and natural copy. Do not add decorative fake metrics, generic marketing text, gradients, glowing elements, or excessive animation.
- Keep motion short and purposeful: page fade, expanding disclosure, toast, and progress changes. Respect reduced-motion preferences.
- Use Geist/Satoshi-style sans typography with a monospace face only for paths, hashes, IDs, and measured values.
- Meet WCAG 2.2 AA, support keyboard use, and maintain usable layouts from 320px mobile through wide desktop.

## Phase F1 — Repair the information architecture (P0)

### Global shell

- Keep the five primary destinations, but visually group “Overview / Inventory / Reports / CBOM” as review work and make “New scan” the distinct primary action.
- Replace the text-heavy brand lockup with a compact logo and product name; move “Discovery Assurance” into an unobtrusive subtitle or tooltip.
- Turn “Local & explainable” into a small system-status control showing API, database, and scanner health instead of a decorative badge.
- Put account/sign-out actions into a compact user menu; keep theme selection beside it.
- Make the header responsive: desktop navigation, mobile menu, no clipped labels or horizontal scrolling.
- Use one consistent content width and vertical rhythm on every page.

### Page hierarchy

- Every page gets: eyebrow/context, clear H1, one-sentence purpose, primary action, then content.
- Remove large empty areas that push important content below the fold.
- Use consistent breadcrumbs only on detail pages, not top-level pages.

Acceptance: users can identify the current page and its primary action within three seconds at desktop and mobile widths.

## Phase F2 — Make Overview decision-oriented (P0)

- Replace generic metric cards with a compact assurance summary:
  - latest scan status and time;
  - confirmed cryptographic operations;
  - capability-only findings;
  - migration priorities;
  - scan coverage and failures.
- Add a “Needs attention” section showing the five highest-priority findings with algorithm, location, reason, confidence, and direct link.
- Show risk distribution and evidence composition as small readable visuals with adjacent values; never require hover to understand them.
- Add a recent-scans timeline with status, repository, duration, coverage, and findings.
- Give first-time users one clear empty-state action: “Run your first scan.”
- When the API fails, show an inline recovery panel with retry, readiness status, request ID, and a useful cause such as “database migration required.”

Acceptance: the Overview answers “what changed and what needs attention?” without visiting another page.

## Phase F3 — Simplify Inventory (P0)

- Default to a readable table; keep alternate views only if they provide a real workflow benefit.
- Group controls into one toolbar: search, risk, evidence type, algorithm, repository/scan, and sort.
- Replace the unclear “Quantum vulnerable” text control with an explicit checkbox or filter chip showing its active state.
- Show active filters as removable chips and provide “Clear all.”
- Use human column names: Finding, Evidence, Location, Confidence, Priority, Action.
- Distinguish confirmed use from declared capability with text and shape, not color alone.
- Keep row actions concise: “View details” as the row link; place editing/export actions in a contextual menu.
- Add pagination or virtualization for large scans, sticky table headers, truncation with accessible full-value disclosure, and a compact mobile list layout.
- Do not show “No assets found” while an API error is active. Error, loading, empty, and filtered-empty states must be mutually exclusive.

Acceptance: a user can find a vulnerable confirmed operation and open its evidence in under four interactions.

## Phase F4 — Rebuild New Scan as a clear three-step task (P0)

- Replace broken text such as `01Enumerate scope` and `1Repository` with properly styled numbered steps.
- Keep the step indicator visually separate from explanatory copy.
- Step 1: repository path, recent-path suggestions, validation, allowed-root guidance.
- Step 2: scan options with safe defaults and plain explanations; hide advanced limits in a disclosure.
- Step 3: review repository and options, then “Start scan.”
- Validate each field inline and never advance when invalid.
- During scanning, replace the form with live progress: current phase, files processed, findings, elapsed time, cancel action, and declared blind spots.
- On completion, show a concise result summary with “View inventory” as primary and “View report” as secondary.
- Recent scans should be a compact table below the task, not visually compete with the scan form.

Acceptance: a first-time user can launch `/test-repo` without needing documentation, and every scan state has a clear next action.

## Phase F5 — Make Reports and CBOM understandable (P1)

### Reports

- Add a report header with scan selector, generated time, export actions, and an explanation of observed operations versus declared capabilities.
- Lead with ranked migration priorities, not an abstract score.
- Each priority explains: why it matters, supporting evidence, recommended replacement, migration effort, and affected location.
- Put evaluation metrics in a clearly labeled “Detection quality” section; distinguish corpus benchmarks from the current repository’s reviewed ground truth.
- Replace blank error screens with retryable panels that preserve the page structure.

### CBOM

- Start with a concise inventory summary and component table.
- Keep Components, Dependency graph, and Raw JSON as clear tabs with counts.
- Make graph controls discoverable and provide a table fallback for keyboard/mobile users.
- Put CycloneDX/JSON export near the page title and state exactly which scan is exported.
- Explain CBOM once in plain language: “A machine-readable inventory of cryptographic components and evidence.”

Acceptance: non-specialists can understand what the report says; specialists can reach raw evidence and exports without losing context.

## Phase F6 — Create a small, coherent design system (P1)

- Consolidate the overlapping CSS files into tokens, base, components, layouts, and page-specific styles.
- Remove duplicate selectors and specificity overrides that caused the login and scan-step regressions.
- Standardize spacing, content widths, typography, borders, radii, shadows, focus rings, buttons, inputs, tables, badges, disclosures, and alerts.
- Keep one accent color and semantic colors for risk/status. Verify light and dark contrast independently.
- Use one icon family only after confirming it is installed; otherwise use simple local SVGs.
- Document component states in a lightweight internal showcase or Storybook-equivalent test page.
- Keep animations limited to transform and opacity and isolate Framer Motion to components that benefit from it.

Acceptance: pages no longer depend on import order accidents, and common components look and behave identically everywhere.

## Phase F7 — Complete accessibility, responsive, and performance QA (P1)

- Test keyboard order, skip link, visible focus, dialogs, disclosures, tables, form errors, toast announcements, and charts.
- Run authenticated Axe checks on every populated route in light and dark modes.
- Validate 320, 375, 768, 1024, 1440, and 1920px widths with no horizontal overflow.
- Add visual regression snapshots for loading, empty, populated, filtered-empty, validation, API error, scan-running, and scan-complete states.
- Replace generic spinners with layout-matched skeletons where content shape is known.
- Lazy-load report charts and raw CBOM views; keep initial interaction fast.
- Resolve the Windows Playwright shutdown leak so E2E returns a clean exit code.

Acceptance: WCAG 2.2 AA automated checks have no serious violations, all target widths pass, and the production bundle stays inside existing budgets.

## Delivery sequence

1. Global shell and design-system cleanup.
2. New Scan workflow.
3. Overview and Inventory.
4. Reports and CBOM.
5. Accessibility, responsive, visual-regression, and performance gates.
6. Continue with the backend/scanner roadmap in `implementation-improvement-plan-2026-09-12.md`.

## Definition of done

- All top-level routes have polished loading, populated, empty, and error states.
- No raw API error string is the only content on a page.
- No broken counters, joined labels, clipped controls, or contradictory states.
- Primary tasks are usable with keyboard and at 375px.
- UI tests, E2E workflows, authenticated accessibility checks, build, lint, formatting, and performance budgets pass.
- Five people unfamiliar with ECDAT can start a scan and identify the highest-priority finding without coaching.

