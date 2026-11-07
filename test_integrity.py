import json
import hashlib
import io
import contextlib
from unittest import mock
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
        self.outtemp = tempfile.TemporaryDirectory(); self.addCleanup(self.outtemp.cleanup)
        self.out = Path(self.outtemp.name)
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
        lock = self.out / '.docintegrity-active'
        lock.write_text(json.dumps({'pdf': str(self.pdf)})); lock.chmod(0o600)
        cmd = [sys.executable, '-B', str(Path(g.__file__)), '--root', str(self.root), '--source-root', str(self.root), '--since', '9999999999', '--lock-file', str(lock)]
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(run.stdout, '')
        (self.root/'reference.txt').unlink()
        run = subprocess.run(cmd, capture_output=True, text=True)
        self.assertEqual(json.loads(run.stdout)['decision'], 'block')
        self.assertTrue(lock.exists())
    def test_gate_malformed_lock_fails_closed(self):
        lock = self.out / '.docintegrity-active'; lock.write_text('{}'); lock.chmod(0o600)
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

    def gate(self, *extra, stdin=None):
        cmd = [sys.executable, '-B', str(Path(g.__file__)), '--root', str(self.root), '--source-root', str(self.root), '--since', '0', *extra]
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True)
    def test_configurable_include_exclude_and_prune(self):
        (self.root/'other.pdf').write_bytes(b'invented')
        (self.root/'ignore.pdf').write_bytes(b'invented')
        sub = self.root/'archive'; sub.mkdir(); (sub/'older.pdf').write_bytes(b'invented')
        selected = g.collect_candidates([self.root], 0, ['*.pdf'], ['ignore.pdf'], ['archive'])
        self.assertEqual({p.name for p in selected}, {'example.filled.pdf', 'other.pdf'})
    def test_host_payload_is_explicit_and_bounded(self):
        transcript = self.root/'transcript.fixture'; transcript.write_text('invented')
        run = self.gate('--host-stdin', '--ledger', str(self.out), stdin=json.dumps({'session_id':'session-example', 'transcript_path':str(transcript)}))
        self.assertEqual(run.stdout, '')
        events = list(self.out.glob('event-*.json')); self.assertEqual(len(events), 1)
        self.assertEqual(json.loads(events[0].read_text())['session_id'], 'session-example')
        run = self.gate('--host-stdin', stdin='{"session_id": 3}')
        self.assertEqual(json.loads(run.stdout)['decision'], 'block')
        run = self.gate('--host-stdin', stdin=json.dumps({'transcript_path':str(self.out/'missing')}))
        self.assertEqual(json.loads(run.stdout)['decision'], 'block')
    def test_report_and_event_history_are_append_safe(self):
        for _ in range(2): self.assertEqual(self.gate('--audit-dir', str(self.out), '--ledger', str(self.out)).stdout, '')
        events, reports = list(self.out.glob('event-*.json')), list(self.out.glob('audit-*.json'))
        self.assertEqual((len(events), len(reports)), (2, 2))
        event = json.loads(events[0].read_text())
        self.assertNotIn('Example', json.dumps(event)); self.assertNotIn('reference.txt', json.dumps(event))
        self.assertEqual(event['event'], 'audit_summary'); self.assertFalse(event['audit_failed']); self.assertNotIn('blocked', event)
        self.assertEqual(json.loads(reports[0].read_text())['audit']['overall'], 'PASS')
    def test_concurrent_history_creates_distinct_records(self):
        cmd = [sys.executable, '-B', str(Path(g.__file__)), '--root', str(self.root), '--source-root', str(self.root), '--since', '0', '--ledger', str(self.out)]
        children = [subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(4)]
        for child in children:
            stdout, stderr = child.communicate(); self.assertEqual(stdout, '', stderr)
        self.assertEqual(len(list(self.out.glob('event-*.json'))), 4)
    def test_output_roots_and_existing_aliases_are_never_overwritten(self):
        original = self.pdf.read_bytes()
        self.assertEqual(json.loads(self.gate('--audit-dir', str(self.root)).stdout)['decision'], 'block')
        alias = self.root/'out-alias'; alias.symlink_to(self.out, target_is_directory=True)
        self.assertEqual(json.loads(self.gate('--ledger', str(alias)).stdout)['decision'], 'block')
        target = self.out/('event-' + 'a'*32 + '.json'); target.write_bytes(b'keep')
        with mock.patch('stop_gate.uuid.uuid4') as ident:
            ident.return_value.hex = 'a'*32
            with self.assertRaises(FileExistsError): g.append_event(self.out, {'event':'test'}, [self.root])
        self.assertEqual(target.read_bytes(), b'keep'); self.assertEqual(self.pdf.read_bytes(), original)
    def test_filing_receipt_is_required_and_matches_bytes(self):
        self.assertEqual(json.loads(self.gate('--require-filing').stdout)['decision'], 'block')
        receipt = self.root/'receipt.json'; receipt.write_text(json.dumps({'receipts':[{'sha256':'0'*64, 'status':'filed'}]}))
        self.assertEqual(json.loads(self.gate('--require-filing', '--filing-receipt', str(receipt)).stdout)['decision'], 'block')
        digest = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        receipt.write_text(json.dumps({'receipts':[{'sha256':digest, 'status':'filed'}, {'sha256':digest, 'status':'rejected'}]}))
        self.assertEqual(json.loads(self.gate('--require-filing', '--filing-receipt', str(receipt)).stdout)['decision'], 'block')
        receipt.write_text(json.dumps({'receipts':[{'sha256':digest, 'status':'filed'}]}))
        self.assertEqual(self.gate('--require-filing', '--filing-receipt', str(receipt)).stdout, '')
        self.assertEqual(json.loads(self.gate('--require-filing', '--override', '--ledger', str(self.out)).stdout)['decision'], 'block')
    def test_lock_clears_only_after_audit_filing_and_outputs(self):
        lock=self.out/'.docintegrity-active'; lock.write_text(json.dumps({'pdf':str(self.pdf)})); lock.chmod(0o600)
        receipt=self.root/'receipt.json'; receipt.write_text(json.dumps({'receipts':[]}))
        args=['--lock-file',str(lock),'--clear-lock-on-pass','--audit-dir',str(self.out),'--ledger',str(self.out),'--require-filing','--filing-receipt',str(receipt)]
        self.assertEqual(json.loads(self.gate(*args).stdout)['decision'], 'block'); self.assertTrue(lock.exists())
        receipt.write_text(json.dumps({'receipts':[{'sha256':hashlib.sha256(self.pdf.read_bytes()).hexdigest(), 'status':'filed'}]}))
        self.assertEqual(self.gate(*args).stdout, ''); self.assertFalse(lock.exists())

    def test_lock_cannot_alias_a_field_map_or_linked_input(self):
        field_map = self.root/'fields.json'; original = field_map.read_bytes()
        self.assertEqual(json.loads(self.gate('--lock-file', str(field_map), '--clear-lock-on-pass', '--audit-dir', str(self.out), '--ledger', str(self.out)).stdout)['decision'], 'block')
        self.assertEqual(field_map.read_bytes(), original)
        lock = self.out/'.docintegrity-active'; os.link(field_map, lock)
        self.assertEqual(json.loads(self.gate('--lock-file', str(lock), '--clear-lock-on-pass', '--audit-dir', str(self.out), '--ledger', str(self.out)).stdout)['decision'], 'block')
        self.assertTrue(lock.exists()); self.assertEqual(field_map.read_bytes(), original)
    def test_lock_content_change_during_audit_is_not_cleared(self):
        lock=self.out/'.docintegrity-active'; lock.write_text(json.dumps({'pdf':str(self.pdf)})); lock.chmod(0o600)
        original_audit = g.audit_candidate
        def mutate(pdf, root):
            result = original_audit(pdf, root)
            lock.write_text(json.dumps({'pdf':'another.pdf'}))
            return result
        output = io.StringIO()
        with mock.patch('stop_gate.audit_candidate', side_effect=mutate), contextlib.redirect_stdout(output):
            g.main(['--root',str(self.root),'--source-root',str(self.root),'--since','0','--lock-file',str(lock),'--clear-lock-on-pass','--audit-dir',str(self.out),'--ledger',str(self.out)])
        self.assertEqual(json.loads(output.getvalue())['decision'], 'block')
        self.assertTrue(lock.exists())

    def test_lock_retained_if_report_output_fails(self):
        lock=self.out/'.docintegrity-active'; lock.write_text(json.dumps({'pdf':str(self.pdf)})); lock.chmod(0o600)
        target=self.out/('audit-' + 'b'*32 + '.json'); target.write_bytes(b'keep')
        output=io.StringIO()
        with mock.patch('stop_gate.uuid.uuid4') as ident, contextlib.redirect_stdout(output):
            ident.return_value.hex = 'b'*32
            g.main(['--root',str(self.root),'--source-root',str(self.root),'--since','0','--lock-file',str(lock),'--clear-lock-on-pass','--audit-dir',str(self.out),'--ledger',str(self.out)])
        self.assertEqual(json.loads(output.getvalue())['decision'], 'block')
        self.assertTrue(lock.exists()); self.assertEqual(target.read_bytes(), b'keep')

    def test_changed_pdf_cannot_borrow_an_earlier_audit(self):
        lock=self.out/'.docintegrity-active'; lock.write_text(json.dumps({'pdf':str(self.pdf)})); lock.chmod(0o600)
        changed = b'invented different bytes'
        receipt=self.root/'receipt.json'; receipt.write_text(json.dumps({'receipts':[{'sha256':hashlib.sha256(changed).hexdigest(), 'status':'filed'}]}))
        original_audit = g.audit_candidate
        def replace_after_audit(pdf, root):
            result = original_audit(pdf, root)
            self.assertEqual(result['pdf_sha256'], hashlib.sha256(self.pdf.read_bytes()).hexdigest())
            self.pdf.write_bytes(changed)
            return result
        output=io.StringIO()
        with mock.patch('stop_gate.audit_candidate', side_effect=replace_after_audit), contextlib.redirect_stdout(output):
            g.main(['--root',str(self.root),'--source-root',str(self.root),'--since','0','--lock-file',str(lock),'--clear-lock-on-pass','--audit-dir',str(self.out),'--ledger',str(self.out),'--require-filing','--filing-receipt',str(receipt)])
        self.assertEqual(json.loads(output.getvalue())['decision'], 'block')
        self.assertTrue(lock.exists())

    def test_pdf_changed_after_report_does_not_clear_lock(self):
        lock=self.out/'.docintegrity-active'; lock.write_text(json.dumps({'pdf':str(self.pdf)})); lock.chmod(0o600)
        original_reports=g.write_reports
        def mutate_after_report(directory, protected, results):
            original_reports(directory, protected, results)
            self.pdf.write_bytes(b'changed after audit')
        output=io.StringIO()
        with mock.patch('stop_gate.write_reports', side_effect=mutate_after_report), contextlib.redirect_stdout(output):
            g.main(['--root',str(self.root),'--source-root',str(self.root),'--since','0','--lock-file',str(lock),'--clear-lock-on-pass','--audit-dir',str(self.out),'--ledger',str(self.out)])
        self.assertEqual(json.loads(output.getvalue())['decision'], 'block'); self.assertTrue(lock.exists())

    def test_gate_decision(self):
        self.assertFalse(g.decide_evidence([])['block'])
        self.assertTrue(g.decide_evidence([], anomaly='bad')['block'])
        self.assertTrue(g.decide_evidence([{'audit': {'overall': 'FAIL'}}])['block'])


if __name__ == '__main__': unittest.main()
