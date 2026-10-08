#!/usr/bin/env python3
"""
Phase 4 corpus evaluator — scans every file in scanner/corpora/,
correlates findings with .expected.json labels, and reports per-language
and per-family precision/recall/F1 metrics.

Usage:
    ECC_GATEGUARD=off python scripts/evaluate_corpora.py
"""

from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from typing import Any

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.abspath(__file__))
)
CORPUS_DIR = os.path.join(REPO_ROOT, "scanner", "corpora")
MANIFEST_PATH = os.path.join(CORPUS_DIR, "manifest.json")
RESULTS_PATH = os.path.join(CORPUS_DIR, "results.json")
THRESHOLDS_PATH = os.path.join(CORPUS_DIR, "thresholds.json")

# ---------------------------------------------------------------------------
# Scanner integration
# ---------------------------------------------------------------------------
sys.path.insert(0, REPO_ROOT)

# Algorithm family mapping
FAMILIES: dict[str, str] = {
    "AES": "symmetric",
    "ChaCha20": "symmetric",
    "RSA": "asymmetric",
    "ECDSA": "asymmetric",
    "Ed25519": "asymmetric",
    "ECDH": "key_exchange",
    "DH": "key_exchange",
    "SHA-256": "hash",
    "SHA-512": "hash",
    "SHA-1": "hash",
    "MD5": "hash",
    "BLAKE2": "hash",
    "HMAC": "mac",
    "TLS": "protocol",
    "PBKDF2": "kdf",
    "KDF": "kdf",
    "HKDF": "kdf",
    "scrypt": "kdf",
    "X509": "protocol",
    "ML-KEM": "key_exchange",
    "ML-DSA": "signature",
    "DSA": "signature",
    "3DES": "symmetric",
}


def load_manifest() -> dict[str, Any]:
    with open(MANIFEST_PATH, "r", encoding="utf-8") as f:
        return json.load(f)  # type: ignore[no-any-return]


def scan_corpus(corpus_dir: str) -> dict[str, list[dict[str, Any]]]:
    """Scan all supported files under corpus_dir, return per-path findings."""
    from scanner.main import scan_with_metrics
    evidence, _metrics = scan_with_metrics(corpus_dir)
    # Convert to per-file mapping
    per_file: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (algo, location), assets in evidence.items():
        per_file[location].extend(assets)
    return dict(per_file)


def expected_label_path(source_path: str, corpus_dir: str) -> str | None:
    """Return path to .expected.json for a source file, or None."""
    base = os.path.splitext(source_path)[0] + ".expected.json"
    candidate = os.path.join(corpus_dir, base)
    return candidate if os.path.isfile(candidate) else None


def load_expected(path: str) -> list[dict[str, str]]:
    with open(path, "r", encoding="utf-8") as f:
        data: Any = json.load(f)
    return data.get("expected_assets", [])  # type: ignore[no-any-return]


def load_thresholds() -> dict[str, Any]:
    """Load gate thresholds from checked-in configuration."""
    if not os.path.isfile(THRESHOLDS_PATH):
        return {}
    with open(THRESHOLDS_PATH, "r", encoding="utf-8") as f:
        data: Any = json.load(f)
    return data.get("gates", {})  # type: ignore[no-any-return]


def check_gates(overall: dict[str, Any], thresholds: dict[str, Any]) -> list[str]:
    """Evaluate metrics against configured gates. Returns list of failure messages."""
    failures: list[str] = []
    fail_closed = thresholds.get("enforcement", {}).get("fail_closed", True)

    # Missing required metric keys
    required_keys = ["micro_precision", "micro_recall", "total_true_positives",
                     "total_false_positives", "total_false_negatives"]
    for key in required_keys:
        if key not in overall:
            msg = f"MISSING METRIC: '{key}' is absent from evaluation results."
            if fail_closed:
                failures.append(msg)
            return failures

    # Precision gate
    if "micro_precision" in thresholds:
        minimum = thresholds["micro_precision"].get("minimum")
        if minimum is not None and overall["micro_precision"] < minimum:
            failures.append(
                f"PRECISION GATE FAILED: {overall['micro_precision']:.2%} < "
                f"{minimum:.2%} minimum ({thresholds['micro_precision'].get('description', '')})"
            )

    # Macro precision gate
    if "macro_precision" in thresholds:
        minimum = thresholds["macro_precision"].get("minimum")
        if minimum is not None and overall.get("macro_precision", 0.0) < minimum:
            failures.append(
                f"MACRO PRECISION GATE FAILED: {overall['macro_precision']:.2%} < "
                f"{minimum:.2%} minimum ({thresholds['macro_precision'].get('description', '')})"
            )

    # Recall gate
    if "micro_recall" in thresholds:
        minimum = thresholds["micro_recall"].get("minimum")
        if minimum is not None and overall["micro_recall"] < minimum:
            failures.append(
                f"RECALL GATE FAILED: {overall['micro_recall']:.2%} < "
                f"{minimum:.2%} minimum ({thresholds['micro_recall'].get('description', '')})"
            )

    # Negative accuracy gate
    if "negative_accuracy" in thresholds:
        minimum = thresholds["negative_accuracy"].get("minimum")
        if minimum is not None and overall.get("negative_accuracy", 0.0) < minimum:
            failures.append(
                f"NEGATIVE ACCURACY GATE FAILED: {overall['negative_accuracy']:.2%} < "
                f"{minimum:.2%} minimum ({thresholds['negative_accuracy'].get('description', '')})"
            )

    # Regression gates
    if "maximum_regression" in thresholds and overall.get("total_true_positives", 0) > 0:
        reg = thresholds["maximum_regression"]
        prev = thresholds.get("_previous_baseline", {})
        prev_precision = prev.get("micro_precision")
        prev_recall = prev.get("micro_recall")
        prev_neg_acc = prev.get("negative_accuracy")

        if prev_precision is not None:
            max_reg = reg.get("micro_precision", 0.0)
            if prev_precision - overall["micro_precision"] > max_reg:
                failures.append(
                    f"PRECISION REGRESSION: {prev_precision:.2%} -> "
                    f"{overall['micro_precision']:.2%} (max drop: {max_reg:.2%})"
                )
        if prev_recall is not None:
            max_reg = reg.get("micro_recall", 0.0)
            if prev_recall - overall["micro_recall"] > max_reg:
                failures.append(
                    f"RECALL REGRESSION: {prev_recall:.2%} -> "
                    f"{overall['micro_recall']:.2%} (max drop: {max_reg:.2%})"
                )
        if prev_neg_acc is not None:
            max_reg = reg.get("negative_accuracy", 0.0)
            if prev_neg_acc - overall.get("negative_accuracy", 0.0) > max_reg:
                failures.append(
                    f"NEGATIVE ACCURACY REGRESSION: {prev_neg_acc:.2%} -> "
                    f"{overall['negative_accuracy']:.2%} (max drop: {max_reg:.2%})"
                )

    return failures


def correlate(
    findings: list[dict[str, Any]], expected: list[dict[str, str]]
) -> dict[str, Any]:
    """Compare scanner findings against expected assets."""
    found_algorithms = {asset["algorithm"] for asset in findings}

    expected_algos = set()
    for exp in expected:
        algo = exp["algorithm"]
        expected_algos.add(algo)

    tp = found_algorithms & expected_algos
    fp = found_algorithms - expected_algos
    fn = expected_algos - found_algorithms

    precision = len(tp) / (len(tp) + len(fp)) if (tp or fp) else (1.0 if not expected else 0.0)
    recall = len(tp) / (len(tp) + len(fn)) if (tp or fn) else (1.0 if not expected else 0.0)
    f1 = (
        2 * precision * recall / (precision + recall)
        if (precision + recall) > 0
        else 0.0
    )

    return {
        "expected": sorted(expected_algos),
        "found": sorted(found_algorithms),
        "true_positives": sorted(tp),
        "false_positives": sorted(fp),
        "false_negatives": sorted(fn),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def compute_aggregate(case_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate per-case metrics into macro-averages."""
    precisions = [r["precision"] for r in case_results if r["type"] == "positive"]
    recalls = [r["recall"] for r in case_results if r["type"] == "positive"]
    f1s = [r["f1"] for r in case_results if r["type"] == "positive"]

    total_tp = sum(len(r["true_positives"]) for r in case_results if r["type"] == "positive")
    total_fp = sum(len(r["false_positives"]) for r in case_results if r["type"] == "positive")
    total_fn = sum(len(r["false_negatives"]) for r in case_results if r["type"] == "positive")

    micro_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) else 0.0
    micro_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) else 0.0
    micro_f1 = (
        2 * micro_precision * micro_recall / (micro_precision + micro_recall)
        if (micro_precision + micro_recall) > 0
        else 0.0
    )

    # Negative case accuracy
    neg_total = sum(1 for r in case_results if r["type"] == "negative")
    neg_correct = sum(1 for r in case_results if r["type"] == "negative"
                      and not r["found"] and not r["false_positives"])
    neg_accuracy = neg_correct / neg_total if neg_total else 0.0

    return {
        "macro_precision": round(sum(precisions) / len(precisions), 4) if precisions else 0.0,
        "macro_recall": round(sum(recalls) / len(recalls), 4) if recalls else 0.0,
        "macro_f1": round(sum(f1s) / len(f1s), 4) if f1s else 0.0,
        "micro_precision": round(micro_precision, 4),
        "micro_recall": round(micro_recall, 4),
        "micro_f1": round(micro_f1, 4),
        "total_true_positives": total_tp,
        "total_false_positives": total_fp,
        "total_false_negatives": total_fn,
        "negative_accuracy": round(neg_accuracy, 4),
        "negative_correct": neg_correct,
        "negative_total": neg_total,
    }


def run() -> None:
    print("Loading manifest...")
    manifest = load_manifest()

    if "cases" not in manifest or not manifest["cases"]:
        raise SystemExit(
            "INVALID MANIFEST: manifest.json must contain a non-empty 'cases' array."
        )

    # Validate case structure
    for case in manifest["cases"]:
        if "type" not in case or "path" not in case:
            raise SystemExit(
                f"INVALID MANIFEST: case '{case.get('id', 'unknown')}' missing 'type' or 'path'."
            )
        if case["type"] not in ("positive", "negative"):
            raise SystemExit(
                f"INVALID MANIFEST: case '{case.get('id', 'unknown')}' has invalid type '{case['type']}'."
            )

    print(f"Scanning corpus ({len(manifest['cases'])} cases)...")
    findings_map = scan_corpus(CORPUS_DIR)

    case_results = []
    for case in manifest["cases"]:
        source_rel = case["path"]
        source_rel_norm = os.path.normpath(source_rel)
        source_abs = os.path.join(CORPUS_DIR, source_rel_norm)
        label_path = expected_label_path(source_rel_norm, CORPUS_DIR)

        case_findings = findings_map.get(source_abs, [])
        if not case_findings:
            # Try with forward-slash path as fallback (for cross-platform manifests)
            source_abs_alt = os.path.join(CORPUS_DIR, *source_rel.split("/"))
            case_findings = findings_map.get(source_abs_alt, [])

        if case["type"] == "positive":
            if not label_path:
                raise SystemExit(
                    f"MISSING LABEL: positive case '{case['id']}' at '{source_rel}' "
                    f"has no .expected.json. Every positive case must have a reviewed "
                    f"expectation file or be reclassified as negative."
                )
            expected = load_expected(label_path)
            corr = correlate(case_findings, expected)
            # Validate expected labels are well-formed
            if not expected:
                raise SystemExit(
                    f"EMPTY LABELS: positive case '{case['id']}' has empty expected_assets. "
                    f"Add reviewed algorithm labels or reclassify as negative."
                )
        else:
            # Negative case — expect zero findings
            found_algos = list({a["algorithm"] for a in case_findings})
            corr = {
                "expected": [],
                "found": sorted(found_algos),
                "true_positives": [],
                "false_positives": sorted(found_algos),
                "false_negatives": [],
                "precision": 0.0 if found_algos else 1.0,
                "recall": 1.0,
                "f1": 0.0 if found_algos else 1.0,
            }

        result = {
            "id": case["id"],
            "language": case["language"],
            "type": case["type"],
            "family": case.get("family", "unknown"),
            "path": source_rel,
            "findings_count": len(case_findings),
            **corr,
        }
        case_results.append(result)

    # Per-language aggregation
    by_language: dict[str, list[dict]] = defaultdict(list)  # type: ignore[type-arg]
    for r in case_results:
        by_language[r["language"]].append(r)

    per_language = {}
    for lang, results in sorted(by_language.items()):
        pos = [r for r in results if r["type"] == "positive"]
        agg = compute_aggregate(pos)
        per_language[lang] = {
            **agg,
            "case_count": len(results),
            "positive_cases": len(pos),
            "negative_cases": len(results) - len(pos),
        }

    overall = compute_aggregate(case_results)

    # Gate check
    thresholds = load_thresholds()
    gate_failures = check_gates(overall, thresholds)
    overall["gates_passed"] = len(gate_failures) == 0
    overall["gate_failures"] = gate_failures

    report = {
        "schema_version": 1,
        "corpus_name": manifest["corpus_name"],
        "evaluated_at": "2026-09-12",
        "overall": overall,
        "per_language": per_language,
        "cases": case_results,
    }

    with open(RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    print(f"\n{'='*60}")
    print("  PHASE 4 CORPORA EVALUATION RESULTS")
    print(f"{'='*60}")
    print(f"  Total cases:     {len(case_results)}")
    print(f"  Positive cases:  {sum(1 for r in case_results if r['type']=='positive')}")
    print(f"  Negative cases:  {sum(1 for r in case_results if r['type']=='negative')}")
    print()
    print("  MICRO (all positive cases):")
    print(f"    Precision: {overall['micro_precision']:.2%}")
    print(f"    Recall:    {overall['micro_recall']:.2%}")
    print(f"    F1:        {overall['micro_f1']:.2%}")
    print(f"    TP={overall['total_true_positives']} FP={overall['total_false_positives']} FN={overall['total_false_negatives']}")
    print()
    print(f"  Negative accuracy: {overall['negative_accuracy']:.2%}")
    print()
    print("  PER-LANGUAGE:")
    for lang, stats in sorted(per_language.items()):
        print(f"    {lang:12s}: P={stats['micro_precision']:.2%} R={stats['micro_recall']:.2%} F1={stats['micro_f1']:.2%} "
              f"(cases={stats['case_count']})")
    print()
    if gate_failures:
        print("  GATE FAILURES:")
        for msg in gate_failures:
            print(f"    * {msg}")
        print()
        raise SystemExit(f"Corpus evaluation FAILED: {len(gate_failures)} gate(s) not met.")
    else:
        print("  GATES: PASSED")
    print(f"{'='*60}")
    print(f"  Results written to: {RESULTS_PATH}")


if __name__ == "__main__":
    run()
