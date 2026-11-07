import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
import fitz
import verify_form_fill as v
import stop_gate as g


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        self.fields = [{'field': 'name', 'required': True}, {'field': 'note', 'required': False}]
        self.actual = {'name': 'Example', 'note': ''}
        self.plan = [{'field': 'name', 'value': 'Example', 'source': 'reference.txt'}]
        self.exists = lambda source: source == 'reference.txt'

    def audit(self):
        return v.audit_fields(self.fields, self.plan, self.actual, self.exists)

    def has(self, verdict):
        self.assertIn(verdict, [item['verdict'] for item in self.audit()['violations']])

    def test_matching_and_optional_blank(self): self.assertEqual(self.audit()['overall'], 'PASS')
    def test_mismatch(self): self.actual['name'] = 'Other'; self.has('MISMATCH')
    def test_missing_source(self): self.plan[0]['source'] = ''; self.has('HALLUCINATION')
    def test_unresolved_source(self): self.plan[0]['source'] = 'missing.txt'; self.has('SOURCE_UNAVAILABLE')
    def test_no_checker_is_not_a_verified_source(self):
        self.assertEqual(v.audit_fields(self.fields, self.plan, self.actual)['overall'], 'FAIL')
    def test_required_blank_without_flag(self): self.plan = []; self.actual['name'] = ''; self.has('UNVERIFIED_BLANK')
    def test_flagged_blank(self):
        self.plan = [{'field': 'name', 'status': 'NEEDS_FOUNDER_INPUT'}]; self.actual['name'] = ''
        result = self.audit(); self.assertEqual(result['overall'], 'PASS'); self.assertEqual(result['needs_founder_input'], ['name'])
    def test_flagged_but_filled(self):
        self.plan = [{'field': 'name', 'status': 'NEEDS_FOUNDER_INPUT'}]; self.has('MISMATCH')
    def test_unknown_plan(self): self.plan.append({'field': 'not-in-map'}); self.has('CONFIG_ERROR')
    def test_duplicate_plan(self): self.plan.insert(0, {'field': 'name', 'value': 'wrong', 'source': 'reference.txt'}); self.has('CONFIG_ERROR')
    def test_duplicate_field_map(self): self.fields.append({'field': 'name', 'required': True}); self.has('CONFIG_ERROR')
    def test_actual_field_missing_from_map(self): self.actual['extra'] = 'Example'; self.has('CONFIG_ERROR')
    def test_map_field_missing_from_pdf(self): del self.actual['note']; self.has('CONFIG_ERROR')
    def test_empty_map_never_passes(self): self.fields = []; self.has('CONFIG_ERROR')
    def test_undeclared_required_policy(self): del self.fields[0]['required']; self.has('CONFIG_ERROR')
    def test_bad_input_shape(self): self.plan = {}; self.has('CONFIG_ERROR')
    def test_container_plan_value_rejected(self): self.plan[0]['value'] = {}; self.has('CONFIG_ERROR')
    def test_nonstring_source_rejected(self): self.plan[0]['source'] = ['reference.txt']; self.has('CONFIG_ERROR')
    def test_unknown_status_rejected(self): self.plan[0]['status'] = 'maybe'; self.has('CONFIG_ERROR')
    def test_flagged_field_with_planned_value_rejected(self): self.plan[0]['status'] = 'NEEDS_FOUNDER_INPUT'; self.actual['name'] = ''; self.has('CONFIG_ERROR')
    def test_actual_value_without_plan(self): self.plan = []; self.has('HALLUCINATION')
    def test_empty_planned_value(self): self.plan[0]['value'] = ''; self.has('CONFIG_ERROR')


class EndToEndTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.pdf = self.root / 'example.filled.pdf'
        doc = fitz.open(); page = doc.new_page(); widget = fitz.Widget()
        widget.field_name = 'name'; widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
        widget.rect = fitz.Rect(40, 40, 250, 70); widget.field_value = 'Example'; page.add_widget(widget)
        doc.save(self.pdf); doc.close()
        (self.root / 'reference.txt').write_text('Invented source content.', encoding='utf-8')
        (self.root / 'fields.json').write_text(json.dumps([{'field':'name', 'required':True}]))
        (self.root / 'fill_plan.json').write_text(json.dumps([{'field':'name', 'value':'Example', 'source':'reference.txt'}]))

    def test_pdf_readback(self):
        result = v.verify_file(self.pdf, self.root/'fields.json', self.root/'fill_plan.json', self.root)
        self.assertEqual(result['overall'], 'PASS'); self.assertEqual(v.read_pdf_field_values(self.pdf), {'name': 'Example'})
    def test_missing_source_path(self):
        (self.root/'reference.txt').unlink()
        self.assertEqual(v.verify_file(self.pdf, self.root/'fields.json', self.root/'fill_plan.json', self.root)['overall'], 'FAIL')
    def test_source_escape_rejected(self):
        check = v.source_checker(self.root)
        self.assertFalse(check('../other')); self.assertFalse(check('/etc/passwd'))
    def test_cli(self):
        cmd = [sys.executable, '-B', str(Path(v.__file__)), '--pdf', str(self.pdf), '--fields', str(self.root/'fields.json'), '--plan', str(self.root/'fill_plan.json'), '--source-root', str(self.root), '--json']
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0, run.stderr); self.assertEqual(json.loads(run.stdout)['overall'], 'PASS')
    def test_gate_audits_and_blocks_missing_sidecar(self):
        self.assertEqual(g.audit_candidate(self.pdf, self.root)['overall'], 'PASS')
        (self.root/'fields.json').unlink()
        self.assertEqual(g.audit_candidate(self.pdf, self.root)['overall'], 'FAIL')
    def test_gate_collects_flat_or_unreadable_candidates(self):
        (self.root/'bad.filled.pdf').write_bytes(b'not a pdf')
        self.assertEqual(len(g.collect_candidates([self.root], 0)), 2)
        cmd = [sys.executable, '-B', str(Path(g.__file__)), '--root', str(self.root), '--source-root', str(self.root), '--since', '0']
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(run.returncode, 0); self.assertEqual(json.loads(run.stdout)['decision'], 'block')
    def test_gate_missing_root_fails_closed(self):
        cmd = [sys.executable, '-B', str(Path(g.__file__)), '--root', str(self.root/'missing'), '--source-root', str(self.root)]
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(json.loads(run.stdout)['decision'], 'block')
    def test_gate_rechecks_explicit_lock_even_if_old(self):
        lock = self.root / 'active.json'
        lock.write_text(json.dumps({'pdf': self.pdf.name}))
        cmd = [sys.executable, '-B', str(Path(g.__file__)), '--root', str(self.root), '--source-root', str(self.root), '--since', '9999999999', '--lock-file', str(lock)]
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(run.stdout, '')
        (self.root/'reference.txt').unlink()
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(json.loads(run.stdout)['decision'], 'block')
        self.assertTrue(lock.exists())
    def test_gate_malformed_lock_fails_closed(self):
        lock = self.root / 'active.json'; lock.write_text('{}')
        cmd = [sys.executable, '-B', str(Path(g.__file__)), '--root', str(self.root), '--source-root', str(self.root), '--lock-file', str(lock)]
        self.assertEqual(json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout)['decision'], 'block')
    def test_gate_override_requires_ledger(self):
        cmd = [sys.executable, '-B', str(Path(g.__file__)), '--root', str(self.root), '--source-root', str(self.root), '--override']
        self.assertEqual(json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout)['decision'], 'block')

    def test_audit_output_refuses_existing_files_and_aliases(self):
        alias = self.root / 'alias.json'; os.link(self.pdf, alias)
        original = self.pdf.read_bytes()
        for target in (self.pdf, alias, self.root / 'fields.json'):
            cmd = [sys.executable, '-B', str(Path(v.__file__)), '--pdf', str(self.pdf), '--fields', str(self.root/'fields.json'), '--plan', str(self.root/'fill_plan.json'), '--source-root', str(self.root), '--audit-out', str(target)]
            self.assertEqual(subprocess.run(cmd, capture_output=True).returncode, 3)
            self.assertEqual(self.pdf.read_bytes(), original)
    def test_hook_ledger_refuses_existing_input(self):
        original = self.pdf.read_bytes()
        cmd = [sys.executable, '-B', str(Path(g.__file__)), '--root', str(self.root), '--source-root', str(self.root), '--override', '--ledger', str(self.pdf)]
        self.assertEqual(json.loads(subprocess.run(cmd, capture_output=True, text=True).stdout)['decision'], 'block')
        self.assertEqual(self.pdf.read_bytes(), original)
    def test_hook_nan_and_broken_lock_fail_closed(self):
        cmd = [sys.executable, '-B', str(Path(g.__file__)), '--root', str(self.root), '--source-root', str(self.root)]
        self.assertEqual(json.loads(subprocess.run(cmd + ['--since', 'nan'], capture_output=True, text=True).stdout)['decision'], 'block')
        lock = self.root/'broken-lock'; lock.symlink_to(self.root/'missing')
        self.assertEqual(json.loads(subprocess.run(cmd + ['--lock-file', str(lock)], capture_output=True, text=True).stdout)['decision'], 'block')
    def test_duplicate_actual_pdf_field_names_rejected(self):
        from pypdf import PdfWriter
        from pypdf.generic import NameObject, TextStringObject, DictionaryObject, ArrayObject
        writer = PdfWriter(); writer.add_blank_page(width=200, height=200)
        fields = ArrayObject()
        for value in ('First', 'Second'):
            field = DictionaryObject({NameObject('/FT'): NameObject('/Tx'), NameObject('/T'): TextStringObject('same'), NameObject('/V'): TextStringObject(value)})
            fields.append(writer._add_object(field))
        writer._root_object[NameObject('/AcroForm')] = writer._add_object(DictionaryObject({NameObject('/Fields'): fields}))
        duplicate = self.root/'duplicate.pdf'
        with duplicate.open('wb') as handle: writer.write(handle)
        with self.assertRaisesRegex(ValueError, 'duplicate'): v.read_pdf_field_values(duplicate)

    def test_duplicate_json_members_rejected(self):
        (self.root/'fill_plan.json').write_text('[{"field":"bad","field":"name","value":"Example","source":"reference.txt"}]')
        with self.assertRaisesRegex(ValueError, 'Duplicate JSON'): v.verify_file(self.pdf, self.root/'fields.json', self.root/'fill_plan.json', self.root)
    def test_unsafe_preferred_sidecar_never_falls_back(self):
        preferred = self.pdf.with_suffix('').with_name(self.pdf.stem + '.fill_plan.json')
        preferred.symlink_to(self.root/'missing')
        with self.assertRaisesRegex(ValueError, 'Unsafe sidecar'): g.audit_candidate(self.pdf, self.root)
    def test_source_symlinks_are_rejected(self):
        (self.root/'alias').symlink_to(self.root/'reference.txt')
        self.assertFalse(v.source_checker(self.root)('alias'))

    def test_gate_decision(self):
        self.assertFalse(g.decide_evidence([])['block'])
        self.assertTrue(g.decide_evidence([], anomaly='bad')['block'])
        self.assertTrue(g.decide_evidence([{'audit': {'overall': 'FAIL'}}])['block'])


if __name__ == '__main__': unittest.main()
