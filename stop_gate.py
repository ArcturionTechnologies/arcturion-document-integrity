#!/usr/bin/env python3
"""Opt-in stop hook for explicitly configured AcroForm output roots."""
from __future__ import annotations
import argparse
import fnmatch
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
import uuid
import verify_form_fill as verifier


def collect_candidates(roots, since_ts, includes=None, excludes=None, prune=None):
    """Discover selected PDF names below caller-supplied roots."""
    found = []
    includes = includes or ['*.filled.pdf']
    excludes, prune = excludes or [], prune or []
    def matches(value, patterns):
        return any(fnmatch.fnmatchcase(value.lower(), pattern.lower()) for pattern in patterns)
    for root in roots:
        p = Path(root)
        if p.is_symlink() or not p.is_dir():
            raise ValueError(f'Watch root is not an ordinary directory: {root}')
        def error(exc):
            raise exc
        for parent, dirs, files in os.walk(p, followlinks=False, onerror=error):
            dirs[:] = [d for d in dirs if not d.startswith('.') and not matches(d, prune) and not (Path(parent) / d).is_symlink()]
            for name in files:
                candidate = Path(parent) / name
                relative = candidate.relative_to(p).as_posix()
                if not name.lower().endswith('.pdf') or not (matches(name, includes) or matches(relative, includes)) or matches(name, excludes) or matches(relative, excludes):
                    continue
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


def output_directory(path, protected_roots):
    p = Path(path)
    if p.is_symlink() or not p.is_dir():
        raise ValueError('Output directory must be an existing ordinary directory')
    resolved = p.resolve(strict=True)
    info = resolved.stat()
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise ValueError('Output directory must be owned by the current user and not group/other writable')
    for root in protected_roots:
        base = Path(root).resolve(strict=True)
        if resolved.is_relative_to(base) or base.is_relative_to(resolved):
            raise ValueError('Output directory must be separate from watch and source roots')
    return resolved


def _new_atomic_record(root, prefix, data):
    """Publish one immutable JSON record without modifying an existing file."""
    token = uuid.uuid4().hex
    temporary, target = root / ('.pending-' + token), root / (prefix + token + '.json')
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
            handle.flush()
            os.fsync(handle.fileno())
        os.link(temporary, target, follow_symlinks=False)
        os.unlink(temporary)
        directory = os.open(root, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def append_event(directory, event, protected_roots):
    """Append to history by creating a unique, durable event file."""
    root = output_directory(directory, protected_roots)
    _new_atomic_record(root, 'event-', event)


def write_reports(directory, protected_roots, results):
    root = output_directory(directory, protected_roots)
    for result in results:
        _new_atomic_record(root, 'audit-', result)


def check_filing_receipts(path, results):
    """Match supplied receipt data, without authenticating its issuer."""
    receipt = Path(path)
    if receipt.is_symlink() or not receipt.is_file():
        raise ValueError('Filing receipt is missing or unsafe')
    with receipt.open(encoding='utf-8') as handle:
        data = verifier.strict_json_load(handle)
    if not isinstance(data, dict) or not isinstance(data.get('receipts'), list):
        raise ValueError('Filing receipt requires a receipts list')
    if not results:
        raise ValueError('Filing policy requires at least one selected document')
    for result in results:
        digest = hashlib.sha256(Path(result['path']).read_bytes()).hexdigest()
        matches = [r for r in data['receipts'] if isinstance(r, dict) and r.get('sha256') == digest]
        if len(matches) != 1 or matches[0].get('status') != 'filed':
            raise ValueError('Filing evidence missing, duplicated, or mismatched')


def host_context(roots):
    payload = verifier.strict_json_load(sys.stdin)
    if not isinstance(payload, dict) or not isinstance(payload.get('session_id', ''), str):
        raise ValueError('Host payload must be an object with a text session_id')
    since = time.time() - 14400
    transcript = payload.get('transcript_path')
    if transcript:
        if not isinstance(transcript, str):
            raise ValueError('Transcript path must be text')
        path = Path(transcript)
        if path.is_symlink() or not path.is_file():
            raise ValueError('Transcript metadata unavailable')
        resolved = path.resolve(strict=True)
        if not any(resolved.is_relative_to(Path(root).resolve(strict=True)) for root in roots):
            raise ValueError('Transcript metadata outside selected roots')
        birth = getattr(path.stat(), 'st_birthtime', None)
        if birth:
            since = max(since, float(birth) - 60)
    return payload.get('session_id', ''), since


def main(argv=None):
    ap = argparse.ArgumentParser(description='Opt-in checks of named AcroForm outputs; emits hook block JSON on failure.')
    ap.add_argument('--root', action='append', required=True, help='Explicit watch root; repeatable')
    ap.add_argument('--source-root', required=True)
    ap.add_argument('--since', type=float, help='Unix timestamp; default four hours ago')
    ap.add_argument('--host-stdin', action='store_true', help='Read optional session and transcript metadata from stdin')
    ap.add_argument('--include', action='append', help='Name or root-relative glob; repeatable; default *.filled.pdf')
    ap.add_argument('--exclude', action='append', help='Name or root-relative glob to omit; repeatable')
    ap.add_argument('--prune', action='append', help='Directory-name glob to omit; repeatable')
    ap.add_argument('--max-candidates', type=int, default=10)
    ap.add_argument('--lock-file', help='Optional explicit JSON lock containing a pdf path')
    ap.add_argument('--clear-lock-on-pass', action='store_true')
    ap.add_argument('--audit-dir', help='Separate caller-owned directory for per-document JSON reports')
    ap.add_argument('--ledger', help='Separate private caller-owned directory for append-only event files')
    ap.add_argument('--override', action='store_true', help='Allow without checks only with an explicit ledger')
    ap.add_argument('--require-filing', action='store_true', help='Require matching caller-supplied filing receipts')
    ap.add_argument('--filing-receipt', help='JSON with receipts containing sha256 and status filed; not authenticated')
    args = ap.parse_args(argv)
    result = {'block': True, 'reason': 'Audit was not run.'}
    try:
        if args.max_candidates < 1:
            raise ValueError('max-candidates must be positive')
        if args.since is not None and not math.isfinite(args.since):
            raise ValueError('since must be finite')
        if args.clear_lock_on_pass and (not args.lock_file or not args.audit_dir or not args.ledger):
            raise ValueError('Lock clearing requires an explicit lock, report directory and event directory')
        verifier.source_checker(args.source_root)
        protected = args.root + [args.source_root]
        session_id, host_since = host_context(args.root) if args.host_stdin else ('', time.time() - 14400)
        if args.override:
            if args.require_filing:
                raise ValueError('Override cannot bypass an enabled filing policy')
            if not args.ledger:
                raise ValueError('Override requires a ledger path')
            append_event(args.ledger, {'event': 'override', 'time': time.time(), 'session_id': session_id}, protected)
            result = decide_evidence([], override=True)
        else:
            candidates = collect_candidates(args.root, args.since if args.since is not None else host_since, args.include, args.exclude, args.prune)
            locked = None
            if args.lock_file and (Path(args.lock_file).exists() or Path(args.lock_file).is_symlink()):
                lock_path = Path(args.lock_file)
                if lock_path.name != '.docintegrity-active' or lock_path.is_symlink() or not lock_path.is_file():
                    raise ValueError('Unsafe lock file')
                output_directory(lock_path.parent, protected)
                lock_stat = lock_path.stat()
                if (lock_stat.st_uid != os.geteuid() or lock_stat.st_nlink != 1
                        or stat.S_IMODE(lock_stat.st_mode) & 0o077):
                    raise ValueError('Lock must be a private, owner-controlled, single-link file')
                if args.filing_receipt and Path(args.filing_receipt).exists() and lock_path.samefile(args.filing_receipt):
                    raise ValueError('Lock and filing receipt must be separate')
                lock_bytes = lock_path.read_bytes()
                lock = verifier.strict_json_load(io.StringIO(lock_bytes.decode('utf-8')))
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
            if args.clear_lock_on_pass and locked is None:
                raise ValueError('Requested lock is absent')
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
            if args.require_filing:
                if not args.filing_receipt:
                    raise ValueError('Filing policy requires an explicit receipt')
                check_filing_receipts(args.filing_receipt, results)
            if args.audit_dir:
                write_reports(args.audit_dir, protected, results)
            if args.ledger:
                append_event(args.ledger, {'event': 'audit', 'time': time.time(), 'session_id': session_id, 'candidates': len(candidates), 'blocked': result['block']}, protected)
            if args.clear_lock_on_pass and locked is not None and not result['block']:
                current = lock_path.stat(follow_symlinks=False)
                if (lock_path.is_symlink() or current.st_nlink != 1 or
                        (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) !=
                        (lock_stat.st_dev, lock_stat.st_ino, lock_stat.st_size, lock_stat.st_mtime_ns, lock_stat.st_ctime_ns) or
                        lock_path.read_bytes() != lock_bytes):
                    raise ValueError('Lock changed before clearing')
                lock_path.unlink()
    except Exception as exc:
        result = {'block': True, 'reason': f'Could not complete selected checks: {type(exc).__name__}: {exc}'}
    if result['block']:
        print(json.dumps({'decision': 'block', 'reason': result['reason']}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
