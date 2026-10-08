# Claude handoff prompt — accuracy and design improvements

Copy everything below into Claude while its working directory is:

`C:\Users\Tishan Kumar B\Desktop\ImpactX\ECDAT-ImpactX`

---

Continue improving ECDAT, an Enterprise Cryptographic Discovery & Analysis Tool. Work directly in the existing repository and preserve all unrelated files and user changes.

## Strict scope

This task is implementation and verification only.

- Do not run any Git command.
- Do not create or switch branches.
- Do not commit, push, pull, fetch, merge, rebase, reset, restore, checkout, stash, tag, or amend anything.
- Do not open or modify pull requests or repository settings.
- Do not edit `.git` files.
- Do not add secrets, credentials, `.env` files, databases, virtual environments, `node_modules`, runtime checkouts, or build output.
- Do not make unrelated cleanup changes or broad formatting rewrites.
- Do not install new dependencies unless an improvement genuinely requires one and I explicitly approve it.

## Read first

Before changing code, read these files completely:

1. `docs/accuracy-design-improvement-plan-2026-09-11.md`
2. `MEMORY.md`
3. `ecdat-impactx-project-knowledge.md`
4. `docs/verification-2026-09-10.md`
5. `docs/discovery-accuracy-audit.md`
6. `docs/operation-correlation.md`
7. `docs/accuracy-benchmark-v3.md`
8. `docs/ARCHITECTURE.md`

Then inspect the relevant implementation and tests. Treat documentation as context, not proof; verify claims against the current code.

## Current verified baseline

- The scanner combines Python AST, multi-language rules, dependencies, and X.509 certificate evidence.
- The pipeline is collection → operation-aware correlation (`operation-v2`) → confidence → risk → persistence → API/UI.
- The latest recorded baseline is 118 backend tests passing with 1 skipped, 22 frontend tests passing, and 7 Chromium E2E workflows passing.
- Backend coverage was recorded as 84.95%.
- External benchmark v3 reports 8 TP, 0 FP, and 0 FN, but it is a consumed Java/SHA regression corpus with only eight positive operations. Never present it as general real-world accuracy.
- The built-in evaluation endpoint currently scores `(component, algorithm)` pairs and does not fully validate operation identity, duplicates, usage, key size, or source location.
- Confidence values are evidence-strength scores based on fixed weights, not calibrated probabilities.

## Primary objective

Complete the first accuracy-foundation increment, then improve the design foundation only where the data semantics are stable.

Work in this order:

### Part 1 — Fix unreachable dependency lockfile collectors

There is a confirmed defect in `scanner/main.py`: handlers exist for `package-lock.json`, `Gemfile.lock`, `go.sum`, and `Cargo.lock`, but `_inventory()` does not place those names in the supported-file list. Consequently, the handlers cannot execute through `scan_with_metrics()`.

Use test-driven development:

1. Add failing tests that scan temporary repositories through `scan_with_metrics()`, not by calling collector helpers directly.
2. Cover every implemented manifest/lockfile handler: `requirements.txt`, `pom.xml`, `package-lock.json`, `Gemfile.lock`, `go.sum`, and `Cargo.lock`.
3. Assert that the file is counted in scope, processed, attributed to the dependency collector, and produces expected evidence when valid.
4. Include malformed and negative cases where appropriate. A malformed supported file must affect failure/coverage metrics consistently.
5. Replace duplicated filename routing with a small shared collector registry or equivalent single source of truth. Inventory support and handler dispatch must not drift apart again.
6. Preserve file-count, byte, duration, memory, symlink/junction, evidence-count, redaction, and path-sanitization protections.

Acceptance criteria:

- All implemented dependency lockfiles are reachable through the main scanner.
- Adding a future filename handler requires changing one registry, not separate inventory and dispatch lists.
- Existing collector behavior and coverage semantics remain compatible unless a test proves a correction is needed.

### Part 2 — Improve evaluation correctness

Upgrade evaluation without falsifying or rewriting historical benchmark evidence.

1. Define a versioned operation-level ground-truth schema containing, where applicable:
   - component
   - relative file path
   - line or stable operation anchor
   - algorithm
   - usage
   - key size
   - evidence kind
2. Keep the old component–algorithm metric available and explicitly labeled as legacy/coarse for compatibility.
3. Add operation-level matching that:
   - counts duplicate predictions as false positives;
   - does not give full credit to the right algorithm at the wrong operation;
   - jointly evaluates usage and key size rather than matching them independently across different predictions;
   - reports unavailable when no valid denominator exists;
   - reports false positives, false negatives, and metadata mismatches with safe relative locations.
4. Add per-collector and per-language slices only when the attribution is supported by actual evidence. Do not manufacture precision/recall for missing categories.
5. Update backend schemas, API response types, frontend TypeScript types, tests, and documentation together.
6. Do not modify the frozen external-v3 labels to make new code pass. Mark consumed versus unconsumed corpora clearly.

Acceptance criteria:

- A duplicate operation lowers precision.
- Wrong usage or key size lowers the corresponding metadata metric.
- Missing key-size labels produce “unavailable,” not a perfect result.
- Existing clients can still identify and display the legacy component-level metrics.

### Part 3 — Establish evidence semantics

If Parts 1 and 2 are complete and verified, begin the evidence-kind foundation:

- `observed_operation`: a direct call or configuration selecting an algorithm.
- `declared_capability`: an import or dependency that makes cryptography available but does not prove use.
- `configured_protocol`: TLS/cipher/protocol configuration.
- `artifact_metadata`: certificate or key metadata without inferred runtime behavior.
- `unknown`: retained evidence that cannot be safely promoted.

Requirements:

1. Add the model and contract tests before changing collector output.
2. Stop presenting dependency/import evidence as confirmed execution.
3. Do not discard dependency evidence merely because another algorithm is observed in the same component.
4. Do not infer certificate usage from public-key type alone. Use `unknown` unless key usage, extended key usage, protocol configuration, or operation evidence establishes a role.
5. Preserve exact provenance, parser/rule version, source span, confidence reasons, ambiguity, and blind spots.
6. Version semantic changes so old scans are not silently reinterpreted.

If this part would require a database migration or a breaking API change, stop after producing a concrete migration design and explain the compatibility impact. Do not make the breaking change without approval.

### Part 4 — Design-foundation improvements

Only work on design after the relevant backend/API semantics are stable. This is an analyst console, not a marketing page.

Design direction:

- Quiet forensic workspace.
- Design variance: 6/10.
- Motion intensity: 3/10.
- Visual density: 7/10.
- One restrained teal action accent plus semantic risk colors.
- Risk, confidence, and coverage must be visually and verbally distinct.
- Use motion only to clarify transitions or status; support reduced motion fully.
- Prefer hierarchy, spacing, and dividers over adding more generic cards.

First design increment:

1. Audit current tokens, duplicated selectors, page states, breakpoints, and route layouts.
2. Consolidate design tokens without broadly rewriting every stylesheet.
3. Create or refine shared primitives for page headers, metrics, provenance badges, status, skeletons, empty/error states, and disclosures.
4. Improve the overview so its hierarchy is:
   - urgent migration actions;
   - assurance quality and scan scope;
   - risk distribution;
   - evidence collectors and visibility gaps.
5. Place corpus, granularity, denominator, and consumed/unconsumed status next to every displayed accuracy metric.
6. Remove decorative competition among teal, indigo, and violet; retain amber/red only for semantic states.
7. Replace generic spinners with layout-matched skeletons where the final layout is known.
8. Preserve mobile, dark mode, keyboard navigation, 200% zoom, and reduced-motion behavior.
9. Do not derive CRITICAL/HIGH/MEDIUM/LOW risk labels from confidence. Risk labels must come from persisted risk data.

Avoid excessive animation, neon/outer glows, pure black, oversized headings, decorative gradients, custom cursors, and repetitive equal-card layouts.

## Working method

- Start each bug fix with a failing focused test.
- Make the smallest coherent implementation change.
- Run focused tests after each change.
- Run broader regression gates after a coherent part is complete.
- Inspect failures and fix their cause; never weaken assertions merely to get green output.
- Preserve honest uncertainty. Do not claim testing proves the absence of all bugs.
- Do not silently change historical scan meaning or benchmark labels.
- Keep code, tests, API contracts, types, and relevant documentation synchronized.
- If unrelated existing changes are encountered, preserve them and work around them.

## Verification commands

Run the applicable focused tests during development, then run the complete available gates before handing back:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m pytest --cov=backend --cov-report=term
.\.venv\Scripts\python.exe -m scripts.benchmark_external --manifest benchmarks\external-v3.json --repeat 3
.\.venv\Scripts\ruff.exe check backend scanner scripts tests
cd dashboard
npm run lint
npm run format:check
npm test -- --run
npm run build
npm run test:e2e
cd ..
```

If a tool or environment dependency is unavailable, report the exact limitation and continue with every safe check that can run. Do not install or upgrade dependencies without approval.

## Handoff format

When finished, report:

1. Improvements completed, grouped by accuracy, API/data model, and design.
2. Bugs found and the root cause of each.
3. Files changed.
4. Tests added or updated.
5. Exact verification results, including benchmark scope and denominators.
6. Remaining limitations, risks, and the next smallest improvement.
7. Any migration or compatibility decision that still requires approval.

Do not include Git status, commit instructions, branch information, or push instructions. Leave all repository-history operations to me.

Begin with Part 1. Do not spend the first response restating this prompt; inspect the required files, write the failing regression tests, implement the fix, and verify it.

---
