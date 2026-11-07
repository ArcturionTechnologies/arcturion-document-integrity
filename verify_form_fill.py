#!/usr/bin/env python3
"""Compare a saved AcroForm against an explicit field map and fill plan."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

VIOLATION_VERDICTS = {'HALLUCINATION', 'UNVERIFIED_BLANK', 'MISMATCH', 'CONFIG_ERROR', 'SOURCE_UNAVAILABLE'}


def _norm(value):
    return '' if value is None else str(value).strip()


def audit_fields(field_map, fill_plan, actual_values, source_exists=None):
    """Compare supplied values. A source checker must confirm each cited file exists.

    Existence does not establish a source's accuracy, identity, or authority.
    """
    rows, violations, needs = [], [], []
    def issue(name, code, detail, intended='', actual='', source=''):
        rows.append({'field': name, 'intended': intended, 'actual': actual, 'source': source, 'verdict': code})
        if code in VIOLATION_VERDICTS:
            violations.append({'field': name, 'verdict': code, 'detail': detail})
    if not isinstance(field_map, list) or not field_map or not isinstance(fill_plan, list) or not isinstance(actual_values, dict):
        issue('(document)', 'CONFIG_ERROR', 'A nonempty field map, plan list and actual-values mapping are required')
        return {'overall': 'FAIL', 'rows': rows, 'violations': violations, 'needs_founder_input': needs}
    fields, plans = {}, {}
    for collection, target, kind in ((field_map, fields, 'field map'), (fill_plan, plans, 'plan')):
        for item in collection:
            if not isinstance(item, dict) or not isinstance(item.get('field'), str) or not item['field'].strip():
                issue('(document)', 'CONFIG_ERROR', f'Invalid {kind} entry')
                continue
            name = item['field']
            if name in target:
                issue(name, 'CONFIG_ERROR', f'Duplicate {kind} entry')
                continue
            if kind == 'field map' and not isinstance(item.get('required'), bool):
                issue(name, 'CONFIG_ERROR', 'Field must declare required as a boolean')
            target[name] = item
    for name in set(actual_values) - set(fields):
        issue(name, 'CONFIG_ERROR', 'Saved field is missing from the field map', actual=_norm(actual_values[name]))
    for name in set(fields) - set(actual_values):
        issue(name, 'CONFIG_ERROR', 'Mapped field was not found in saved form')
    for name in set(plans) - set(fields):
        issue(name, 'CONFIG_ERROR', 'Plan field is missing from the field map')
    for name, field in fields.items():
        if name not in actual_values:
            continue
        actual = _norm(actual_values[name]); plan = plans.get(name)
        if plan is None:
            if actual:
                issue(name, 'HALLUCINATION', 'Saved value has no plan', actual=actual)
            elif field.get('required'):
                issue(name, 'UNVERIFIED_BLANK', 'Required field is blank')
            else:
                issue(name, 'OK_EMPTY', '')
            continue
        value, ref, status = plan.get('value'), plan.get('source'), plan.get('status')
        if isinstance(value, (list, dict)) or (ref is not None and not isinstance(ref, str)):
            issue(name, 'CONFIG_ERROR', 'Plan values must be scalar and source references must be strings')
            continue
        if status is not None and status not in ('filled', 'FILLED', 'NEEDS_FOUNDER_INPUT'):
            issue(name, 'CONFIG_ERROR', 'Unrecognized plan status')
            continue
        intended, source = _norm(value), _norm(ref)
        if status == 'NEEDS_FOUNDER_INPUT':
            if intended:
                issue(name, 'CONFIG_ERROR', 'Input-needed field must not have a planned value', intended, actual, source)
                continue
            needs.append(name)
            issue(name, 'MISMATCH' if actual else 'FLAGGED_BLANK', 'Flagged field has a saved value' if actual else '', intended, actual, source)
        elif not intended:
            issue(name, 'CONFIG_ERROR', 'Plan has no intended value', intended, actual, source)
        elif not source:
            issue(name, 'HALLUCINATION', 'Plan has no source reference', intended, actual, source)
        elif source_exists is None or not source_exists(source):
            issue(name, 'SOURCE_UNAVAILABLE', 'Source reference could not be resolved', intended, actual, source)
        elif actual != intended:
            issue(name, 'MISMATCH', 'Saved value differs from plan', intended, actual, source)
        else:
            issue(name, 'VERIFIED', '', intended, actual, source)
    return {'overall': 'FAIL' if violations else 'PASS', 'rows': rows, 'violations': violations, 'needs_founder_input': needs}


def read_pdf_field_values(pdf_path):
    import pypdf
    reader = pypdf.PdfReader(pdf_path)
    root = reader.trailer['/Root']
    form = root.get('/AcroForm')
    if form is None:
        raise ValueError('PDF has no AcroForm')
    form = form.get_object()
    seen_names, ancestors = set(), set()
    visited = 0
    def walk(raw, prefix=''):
        nonlocal visited
        visited += 1
        if visited > 10000:
            raise ValueError('PDF field tree is too large')
        ref = (getattr(raw, 'idnum', None), getattr(raw, 'generation', None))
        obj = raw.get_object()
        identity = ref if ref[0] is not None else id(obj)
        if identity in ancestors:
            raise ValueError('PDF field tree has a cycle')
        ancestors.add(identity)
        try:
            partial = obj.get('/T')
            name = prefix
            if partial is not None:
                part = str(partial)
                if not part:
                    raise ValueError('PDF contains an empty field name')
                name = prefix + '.' + part if prefix else part
                if name in seen_names:
                    raise ValueError('PDF contains duplicate field names')
                seen_names.add(name)
            kids = obj.get('/Kids', [])
            if kids is not None:
                for child in kids:
                    walk(child, name)
        finally:
            ancestors.remove(identity)
    for field in form.get('/Fields', []):
        walk(field)
    fields = reader.get_fields() or {}
    out = {}
    for name, field in fields.items():
        value = field.get('/V')
        if isinstance(value, (list, dict)):
            raise ValueError('Collection-valued PDF fields are not supported')
        out[str(name)] = _norm(value)
    return out


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate JSON member: {key}')
        result[key] = value
    return result


def strict_json_load(handle):
    return json.load(handle, object_pairs_hook=_unique_object, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(f'Non-finite JSON value: {value}')))


def _load_list(path, key):
    with open(path, encoding='utf-8') as handle:
        obj = strict_json_load(handle)
    if isinstance(obj, dict):
        obj = obj.get(key)
    if not isinstance(obj, list):
        raise ValueError(f'{path}: expected a list or an object with a {key} list')
    return obj


def source_checker(root):
    base = Path(root).resolve(strict=True)
    if not base.is_dir():
        raise ValueError('Source root must be an existing directory')
    def exists(reference):
        path = Path(reference)
        if path.is_absolute() or '..' in path.parts:
            return False
        try:
            current = base
            for part in path.parts:
                current = current / part
                if current.is_symlink():
                    return False
            resolved = (base / path).resolve(strict=True)
            return resolved.is_relative_to(base) and resolved.is_file()
        except (OSError, ValueError, RuntimeError):
            return False
    return exists


def verify_file(pdf_path, fields_json_path, fill_plan_json_path, source_root=None):
    fields = _load_list(fields_json_path, 'fields')
    plan = _load_list(fill_plan_json_path, 'fields')
    actual = read_pdf_field_values(pdf_path)
    result = audit_fields(fields, plan, actual, source_checker(source_root) if source_root else None)
    result['pdf'] = str(pdf_path)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description='Compare saved AcroForm fields with an explicit plan.')
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--fields', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--source-root', required=True, help='Directory containing cited files; source values are relative paths')
    parser.add_argument('--audit-out')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        result = verify_file(args.pdf, args.fields, args.plan, args.source_root)
        if args.audit_out:
            with open(args.audit_out, 'x', encoding='utf-8') as handle:
                json.dump(result, handle, indent=2)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print('DOCUMENT INTEGRITY AUDIT:', result['overall'])
            for entry in result['violations']:
                print(f"  [{entry['verdict']}] {entry['field']}: {entry['detail']}")
            if result['needs_founder_input']:
                print('NEEDS_FOUNDER_INPUT:', ', '.join(result['needs_founder_input']))
        return 0 if result['overall'] == 'PASS' else 2
    except Exception as exc:
        print(f'verify_form_fill: error: {type(exc).__name__}: {exc}', file=sys.stderr)
        return 3


if __name__ == '__main__':
    raise SystemExit(main())
