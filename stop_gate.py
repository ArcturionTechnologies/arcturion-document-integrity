#!/usr/bin/env python3
"""Opt-in stop hook for explicitly configured, recent `.filled.pdf` files.

No default watch roots, private paths, automatic installation or filing integration.
Prints a block JSON object on an incomplete audit; otherwise prints nothing.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import sys
import time
import verify_form_fill as verifier


def collect_candidates(roots, since_ts):
    """Discover explicitly named output PDFs under caller-selected roots."""
    found = []
    for root in roots:
        p = Path(root)
        if p.is_symlink() or not p.is_dir():
            raise ValueError(f'Watch root is not an ordinary directory: {root}')
        def error(exc):
            raise exc
        for parent, dirs, files in os.walk(p, followlinks=False, onerror=error):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not (Path(parent) / d).is_symlink()]
            for name in files:
                if not name.lower().endswith('.filled.pdf'):
                    continue
                candidate = Path(parent) / name
                if candidate.is_symlink() or not candidate.is_file():
                    raise ValueError(f'Unsafe candidate: {candidate}')
                if candidate.stat().st_mtime >= since_ts:
                    found.append(candidate)
    return sorted(set(found))


def find_sidecar(pdf, name):
    beside = pdf.with_suffix('').with_name(pdf.stem + '.' + name)
    shared = pdf.parent / name
    for path in (beside, shared):
        if path.is_symlink():
            raise ValueError(f'Unsafe sidecar: {path}')
        if path.exists():
            if not path.is_file():
                raise ValueError(f'Unsafe sidecar: {path}')
            return path
    return None


def audit_candidate(pdf, source_root):
    field_map, plan = find_sidecar(pdf, 'fields.json'), find_sidecar(pdf, 'fill_plan.json')
    if not field_map or not plan:
        return {'overall': 'FAIL', 'violations': [{'field': '(document)', 'verdict': 'MISSING_SIDECAR', 'detail': 'An explicit field map and plan are required'}]}
    return verifier.verify_file(pdf, field_map, plan, source_root)


def decide_evidence(results, *, anomaly=None, override=False):
    if anomaly:
        return {'block': True, 'reason': anomaly}
    if override:
        return {'block': False, 'reason': ''}
    failing = [r for r in results if r['audit'].get('overall') != 'PASS']
    if failing:
        return {'block': True, 'reason': f'{len(failing)} selected document audit(s) failed or could not be inspected.'}
    return {'block': False, 'reason': ''}


def main(argv=None):
    ap = argparse.ArgumentParser(description='Opt-in check of recent named AcroForm outputs; emits hook block JSON on failure.')
    ap.add_argument('--root', action='append', required=True, help='Explicit watch root; repeatable')
    ap.add_argument('--source-root', required=True)
    ap.add_argument('--since', type=float, help='Unix timestamp; default four hours ago')
    ap.add_argument('--max-candidates', type=int, default=10)
    ap.add_argument('--lock-file', help='Optional explicit JSON lock containing a pdf path; re-checks it before allowing stop')
    ap.add_argument('--ledger', help='Optional new JSON-lines ledger; must not already exist')
    ap.add_argument('--override', action='store_true', help='Allow without checks only when a ledger is explicitly supplied')
    args = ap.parse_args(argv)
    result = {'block': True, 'reason': 'Audit was not run.'}
    try:
        if args.max_candidates < 1:
            raise ValueError('max-candidates must be positive')
        if args.since is not None and not math.isfinite(args.since):
            raise ValueError('since must be finite')
        if args.override:
            if not args.ledger:
                raise ValueError('Override requires a ledger path')
            with open(args.ledger, 'x', encoding='utf-8') as handle:
                handle.write(json.dumps({'event': 'override', 'time': time.time()}) + '\n')
            result = decide_evidence([], override=True)
        else:
            verifier.source_checker(args.source_root)
            candidates = collect_candidates(args.root, args.since if args.since is not None else time.time() - 14400)
            if args.lock_file and (Path(args.lock_file).exists() or Path(args.lock_file).is_symlink()):
                lock_path = Path(args.lock_file)
                if lock_path.is_symlink() or not lock_path.is_file():
                    raise ValueError('Unsafe lock file')
                with lock_path.open(encoding='utf-8') as handle:
                    lock = verifier.strict_json_load(handle)
                if not isinstance(lock, dict) or not isinstance(lock.get('pdf'), str):
                    raise ValueError('Lock must identify a PDF')
                locked = Path(lock['pdf'])
                if not locked.is_absolute():
                    locked = lock_path.parent / locked
                if locked.is_symlink() or not locked.is_file():
                    raise ValueError('Locked PDF is missing or unsafe')
                resolved = locked.resolve(strict=True)
                if not any(resolved.is_relative_to(Path(root).resolve(strict=True)) for root in args.root):
                    raise ValueError('Locked PDF is outside the selected roots')
                candidates = sorted(set(candidates + [locked]))
            if len(candidates) > args.max_candidates:
                raise ValueError('Candidate limit exceeded')
            results = []
            for pdf in candidates:
                try:
                    audit = audit_candidate(pdf, args.source_root)
                except Exception as exc:
                    audit = {'overall': 'ERROR', 'violations': [{'field': '(document)', 'verdict': 'READ_ERROR', 'detail': str(exc)}]}
                results.append({'path': str(pdf), 'audit': audit})
            result = decide_evidence(results)
            if args.ledger:
                with open(args.ledger, 'x', encoding='utf-8') as handle:
                    handle.write(json.dumps({'event': 'audit', 'time': time.time(), 'candidates': len(candidates), 'blocked': result['block']}) + '\n')
    except Exception as exc:
        result = {'block': True, 'reason': f'Could not complete selected checks: {type(exc).__name__}: {exc}'}
    if result['block']:
        print(json.dumps({'decision': 'block', 'reason': result['reason']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
