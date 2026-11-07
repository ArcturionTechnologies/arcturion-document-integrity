# Document Integrity

Compare the values saved in a PDF AcroForm with an explicit field map and fill plan. The audit identifies mismatches, unplanned values, required blanks, incomplete or duplicate mappings, and source paths that do not resolve under a chosen directory. A separate opt-in hook can check recently modified, explicitly named form outputs.

**This is a local source candidate for review.** It does not establish that a source is authentic or supports a value, that a document is legally correct, or that the visible page matches its form values. It does not read flattened or scanned form contents, perform OCR, verify digital signatures, or handle non-AcroForm documents. Review the saved document and its sources with a person before relying on it.

## Try an invented form

Use Python 3.10 or newer with `pypdf` installed. Local checks used Python 3.14 and pypdf 6.10; other versions have not been verified here. The synthetic fixture generator and tests also need `PyMuPDF`; see [dependency notices](DEPENDENCIES.md). No real document is needed:

```sh
python3 -m pip install pypdf PyMuPDF
work_dir=$(mktemp -d)
python3 examples/create_fixture.py "$work_dir/example"
python3 verify_form_fill.py \
  --pdf "$work_dir/example/example.filled.pdf" \
  --fields "$work_dir/example/fields.json" \
  --plan "$work_dir/example/fill_plan.json" \
  --source-root "$work_dir/example" --json
```

The output reports `PASS` for the single invented field. The JSON result includes field values and source names. `--audit-out PATH` writes a new file and refuses an existing target; protect the output like the document itself. Exit codes are 0 for no detected violations, 2 for violations, and 3 for input, PDF or output errors. A field explicitly marked `NEEDS_FOUNDER_INPUT` can yield `PASS` while still requiring human input; inspect that list before any completion decision.

Each field map item declares `field` and boolean `required`. The plan lists each intended `field`, `value`, and `source`, or flags a blank with `status: NEEDS_FOUNDER_INPUT`. A `source` is a path relative to `--source-root`; absolute paths, paths escaping the root, symlinked paths, missing files, and unresolved references fail. **A file existing at that path does not prove the value came from it or that the file is trustworthy.** The comparison checks names exposed by the PDF reader; it cannot establish that the supplied required flags are correct or that the PDF reader discovers every possible field structure.

## Optional stop hook

`stop_gate.py` accepts explicit watch roots and a source root. It inspects recently modified names ending in `.filled.pdf`; a selected unreadable, non-AcroForm or unplanned file blocks rather than being silently ignored. A field map and fill plan are required beside each selected PDF as `fields.json` / `fill_plan.json`, or `<pdf-stem>.fields.json` / `<pdf-stem>.fill_plan.json`.

```sh
python3 stop_gate.py --root "$work_dir/example" --source-root "$work_dir/example" --since 0
```

The hook prints `{"decision":"block",...}` on an audit failure and nothing when selected checks pass; normal hook decisions exit 0. Missing command-line arguments are usage errors. An empty scan produces no block and does **not** prove that all documents were inspected. Optional `--lock-file` accepts JSON containing a `pdf` path, rechecks it even when old, and never automatically deletes the lock. Optional `--ledger` creates a new file; an override requires an explicit new ledger. Existing output files are never replaced. Test host behavior before installing a hook: nothing here installs or activates one.

The hook deliberately omits the original environment's private defaults, filing integration and auto-cleared legacy lock. Its roots, time window and naming convention limit what it sees. It is not a universal enforcement or discovery guarantee. See [provenance](PROVENANCE.md) for the adaptations.

## Check and contribute

```sh
python3 -m unittest -v test_integrity
```

The tests create invented temporary forms and documents. An independent review and redistribution terms for the original material remain outstanding; no project license is granted by this candidate.
