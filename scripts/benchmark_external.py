"""Reproducible operation-level evaluation; never executes downloaded source."""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import io
import json
import re
import subprocess
import tempfile
from collections import defaultdict
from pathlib import Path, PureWindowsPath

from backend.services.correlator import correlate
from scanner.main import scan_with_metrics

ROOT = Path(__file__).resolve().parents[1]


def _item_key(item):
    return json.dumps(item, sort_keys=True, default=str)


def _joint_pairs(labels, findings):
    """Return one maximum-score pairing shared by every metadata metric."""
    labels = sorted(enumerate(labels), key=lambda pair: _item_key(pair[1]))
    findings = sorted(enumerate(findings), key=lambda pair: _item_key(pair[1]))
    if not labels or not findings:
        return [], {index for index, _ in labels}, {index for index, _ in findings}
    swapped = len(labels) > len(findings)
    rows, columns = (findings, labels) if swapped else (labels, findings)
    matched = len(rows)

    def score(left, right):
        label, finding = (right, left) if swapped else (left, right)
        usage = label['usage'] == finding['usage']
        key_size = label['key_size'] == finding['key_size']
        # One extra metadata match outweighs every possible usage tie-break.
        return (usage + key_size) * (matched + 1) + usage

    # Hungarian assignment for a rectangular matrix where rows <= columns.
    costs = [[-score(row[1], column[1]) for column in columns] for row in rows]
    row_count, column_count = len(rows), len(columns)
    u, v = [0] * (row_count + 1), [0] * (column_count + 1)
    p, way = [0] * (column_count + 1), [0] * (column_count + 1)
    for row_index in range(1, row_count + 1):
        p[0] = row_index
        column_zero = 0
        minimum = [float('inf')] * (column_count + 1)
        used = [False] * (column_count + 1)
        while True:
            used[column_zero] = True
            current_row = p[column_zero]
            delta, next_column = float('inf'), 0
            for column_index in range(1, column_count + 1):
                if used[column_index]:
                    continue
                current = costs[current_row - 1][column_index - 1] - u[current_row] - v[column_index]
                if current < minimum[column_index]:
                    minimum[column_index] = current
                    way[column_index] = column_zero
                if minimum[column_index] < delta:
                    delta, next_column = minimum[column_index], column_index
            for column_index in range(column_count + 1):
                if used[column_index]:
                    u[p[column_index]] += delta
                    v[column_index] -= delta
                else:
                    minimum[column_index] -= delta
            column_zero = next_column
            if p[column_zero] == 0:
                break
        while True:
            next_column = way[column_zero]
            p[column_zero] = p[next_column]
            column_zero = next_column
            if column_zero == 0:
                break
    pairs = []
    for column_index in range(1, column_count + 1):
        if not p[column_index]:
            continue
        row = rows[p[column_index] - 1]
        column = columns[column_index - 1]
        pairs.append((column[0], row[0]) if swapped else (row[0], column[0]))
    paired_labels = {label_index for label_index, _ in pairs}
    paired_findings = {finding_index for _, finding_index in pairs}
    return sorted(pairs), ({index for index, _ in labels} - paired_labels), (
        {index for index, _ in findings} - paired_findings)


def compare(expected, actual):
    """One-to-one operation matching: duplicates are false positives, not sets."""
    expected_pools = defaultdict(list)
    actual_pools = defaultdict(list)
    for item in expected:
        expected_pools[(item['file'], item['line'], item['algorithm'])].append(item)
    for item in actual:
        actual_pools[(item['file'], item['line'], item['algorithm'])].append(item)
    tp = fp = usage_ok = key_ok = metadata_pair_ok = known_keys = known_keys_ok = 0
    missed, unexpected = [], []
    identities = expected_pools.keys() | actual_pools.keys()
    for identity in sorted(identities, key=_item_key):
        labels = sorted(expected_pools[identity], key=_item_key)
        findings = sorted(actual_pools[identity], key=_item_key)
        pairs, missed_indices, unexpected_indices = _joint_pairs(labels, findings)
        matched = len(pairs)
        tp += matched
        fp += len(unexpected_indices)
        for label_index, finding_index in pairs:
            label, finding = labels[label_index], findings[finding_index]
            usage_ok += label['usage'] == finding['usage']
            key_ok += label['key_size'] == finding['key_size']
            metadata_pair_ok += (label['usage'], label['key_size']) == (
                finding['usage'], finding['key_size'])
            if label['key_size'] is not None:
                known_keys += 1
                known_keys_ok += label['key_size'] == finding['key_size']
        known_keys += sum(labels[index]['key_size'] is not None for index in missed_indices)
        missed.extend(labels[index] for index in sorted(missed_indices))
        unexpected.extend(findings[index] for index in sorted(unexpected_indices))
    fn = len(missed)
    precision = tp / (tp + fp) if tp + fp else None
    recall = tp / (tp + fn) if tp + fn else None
    expected_keys = sum(item['key_size'] is not None for item in expected)
    f1 = (2 * precision * recall / (precision + recall)
          if precision is not None and recall is not None and precision + recall else None)
    return {'tp': tp, 'fp': fp, 'fn': fn, 'precision': precision, 'recall': recall,
            'f1': f1,
            'usage_correct': usage_ok, 'key_size_correct_including_unknown': key_ok,
            'metadata_pair_correct': metadata_pair_ok,
            'matched_operations': tp, 'known_key_size_matches': known_keys,
            'known_key_size_correct': known_keys_ok,
            'expected_known_key_sizes': expected_keys,
            'usage_accuracy_over_expected': usage_ok / len(expected) if expected else None,
            'metadata_pair_accuracy_over_expected': metadata_pair_ok / len(expected) if expected else None,
            'known_key_size_accuracy_over_expected': known_keys_ok / expected_keys if expected_keys else None,
            'missed': missed, 'unexpected': unexpected}


def _verify_revision(source):
    checkout = (ROOT / source['checkout']).resolve()
    result = subprocess.run(
        ['git', '-C', str(checkout), 'rev-parse', 'HEAD'], capture_output=True,
        text=True, check=True,
    )
    actual = result.stdout.strip()
    if actual != source['revision']:
        raise ValueError(f"Source revision mismatch: {source['name']}")


def _validate_manifest(manifest):
    """Validate all untrusted manifest structure before accessing its paths."""
    if not isinstance(manifest, dict) or manifest.get('schema') != 1:
        raise ValueError('Unsupported benchmark manifest schema')
    sources = manifest.get('sources')
    if not isinstance(sources, list) or not sources:
        raise ValueError('Benchmark manifest sources must be a non-empty list')
    algorithms = manifest.get('algorithms')
    if (not isinstance(algorithms, list) or not algorithms
            or any(not isinstance(item, str) or not item for item in algorithms)
            or len(set(algorithms)) != len(algorithms)):
        raise ValueError('Benchmark manifest algorithms must be a unique non-empty string list')
    names = set()
    for source in sources:
        if not isinstance(source, dict):
            raise ValueError('Every benchmark source must be an object')
        name = source.get('name')
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]*', name)
                or name in {'.', '..'} or name.casefold() == 'mixed'
                or name.endswith('.')
                or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', name, re.IGNORECASE)):
            raise ValueError(f'Invalid benchmark source name: {name!r}')
        if name.casefold() in names:
            raise ValueError(f'Duplicate benchmark source name: {name}')
        names.add(name.casefold())
        for field in ('checkout', 'path'):
            value = source.get(field)
            if not isinstance(value, str) or not value:
                raise ValueError(f'Invalid {field} for benchmark source {name}')
            windows_path = PureWindowsPath(value)
            if (Path(value).is_absolute() or windows_path.is_absolute()
                    or '..' in windows_path.parts):
                raise ValueError(f'Invalid {field} for benchmark source {name}')
        if not isinstance(source.get('revision'), str) or not re.fullmatch(r'[0-9a-f]{40}', source['revision']):
            raise ValueError(f'Invalid revision for benchmark source {name}')
        if not isinstance(source.get('sha256'), str) or not re.fullmatch(r'[0-9a-f]{64}', source['sha256']):
            raise ValueError(f'Invalid SHA-256 for benchmark source {name}')
        operations = source.get('operations')
        if not isinstance(operations, list):
            raise ValueError(f'Invalid operations for benchmark source {name}')
        for operation in operations:
            if (not isinstance(operation, dict)
                    or type(operation.get('line')) is not int or operation['line'] <= 0
                    or not isinstance(operation.get('algorithm'), str)
                    or not isinstance(operation.get('usage'), str)
                    or (operation.get('key_size') is not None
                        and (type(operation['key_size']) is not int or operation['key_size'] <= 0))):
                raise ValueError(f'Invalid operation for benchmark source {name}')
            if operation['algorithm'] not in algorithms:
                raise ValueError(f'Out-of-scope operation for benchmark source {name}')
    root = ROOT.resolve()
    for source in sources:
        checkout = (root / source['checkout']).resolve()
        path = (root / source['path']).resolve()
        if not checkout.is_relative_to(root) or not path.is_relative_to(root):
            raise ValueError(f"Benchmark source escapes repository root: {source['name']}")
        if not path.is_relative_to(checkout):
            raise ValueError(f"Benchmark source is outside its checkout: {source['name']}")


def _stage_source(source, directory):
    path = (ROOT / source['path']).resolve()
    _verify_revision(source)
    source_bytes = path.read_bytes()
    if hashlib.sha256(source_bytes).hexdigest() != source['sha256']:
        raise ValueError(f"Source hash mismatch: {source['name']}")
    relative_name = f"{source['name']}/{path.name}"
    destination = Path(directory) / relative_name
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(source_bytes)
    return relative_name


def _finding_line(finding):
    lines = set()
    for item in finding['evidence_list']:
        line = (item.get('span') or {}).get('line_start')
        if line is None:
            line = item.get('evidence', {}).get('line')
        if line is not None:
            lines.add(line)
    return next(iter(lines)) if len(lines) == 1 else None


def _in_scope_findings(findings, algorithms):
    allowed = set(algorithms)
    return [finding for finding in findings if finding['algorithm'] in allowed]


def run(manifest=None):
    if manifest is None:
        manifest = json.loads((ROOT / 'benchmarks/external-v2.json').read_text())
    _validate_manifest(manifest)
    output = {}
    groups = [[s] for s in manifest['sources']] + [manifest['sources']]
    for group in groups:
        name = group[0]['name'] if len(group) == 1 else 'mixed'
        expected = []
        with tempfile.TemporaryDirectory(prefix='ecdat-benchmark-') as directory:
            for source in group:
                relative_name = _stage_source(source, directory)
                expected.extend({'file': relative_name, **x} for x in source['operations'])
            with contextlib.redirect_stdout(io.StringIO()):
                evidence, _ = scan_with_metrics(directory)
                findings = correlate(evidence)
            actual = []
            for finding in _in_scope_findings(findings, manifest['algorithms']):
                relative_name = Path(finding['location']).relative_to(directory).as_posix()
                actual.append({'file': relative_name,
                               'line': _finding_line(finding),
                               'algorithm': finding['algorithm'], 'usage': finding['usage'],
                               'key_size': finding['key_size']})
            output[name] = compare(expected, actual)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--repeat', type=int, choices=(1, 2, 3), default=3)
    parser.add_argument('--manifest', type=Path, default=ROOT / 'benchmarks/external-v2.json')
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text())
    results = [run(manifest) for _ in range(args.repeat)]
    if any(item != results[0] for item in results[1:]):
        raise SystemExit('Benchmark results changed between identical runs')
    print(json.dumps({'runs': args.repeat, 'deterministic': True, 'results': results[0]}, indent=2))
