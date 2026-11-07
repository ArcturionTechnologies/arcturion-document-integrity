# Document Integrity

**Check the PDF you actually saved.**

A form-filling workflow can produce a valid PDF with the wrong values. Document Integrity reads the saved AcroForm, compares it with an explicit field map and fill plan, and reports the differences. An optional local hook can run those checks before a cooperating agent session stops.

It is a small part of Arcturion's approach to agent tooling: make the intended result explicit, inspect the resulting artifact, and keep enough evidence to review what happened.

| Check | What it catches |
| --- | --- |
| Saved values | Values that differ from the plan or appeared without one |
| Field coverage | Missing, duplicate, extra or incompletely declared fields |
| Required blanks | Blank required fields without an explicit human-input flag |
| Source paths | Missing references, absolute paths, symlinks and paths outside the chosen source root |
| Hook: document changes | A selected PDF changing after its audited byte snapshot |
| Hook: local workflow | Selected forms missing sidecars, output errors and unmatched caller-supplied filing receipts |

## Start with one form

From a source checkout, use Python 3.10 or newer:

```sh
python3 -m venv .venv
. .venv/bin/activate
python -m pip install .
document-integrity --help
```

Check a saved form against your own map, plan and source directory:

```sh
document-integrity \
  --pdf example.filled.pdf \
  --fields fields.json \
  --plan fill_plan.json \
  --source-root ./sources --json
```

The command reads the inputs. Add `--audit-out PATH` to create an audit file; an existing output is refused. Reports can contain field values and source names, so store them with the same care as the document.

| Exit code | Meaning |
| --- | --- |
| `0` | No comparison violations detected |
| `2` | Comparison violations detected |
| `3` | Input, PDF or output error |

`PASS` is a comparison result. A deliberately flagged blank can still need human input. The tool does not establish source authenticity, visual correctness, legal correctness, a signature or real filing.

## Try it with invented data

The fixture generator and tests use optional PyMuPDF; its license is separate from this project's MIT terms. Review the [dependency notices](DEPENDENCIES.md), then:

```sh
python -m pip install '.[examples]'
work_dir=$(mktemp -d)
python examples/create_fixture.py "$work_dir/example"
document-integrity \
  --pdf "$work_dir/example/example.filled.pdf" \
  --fields "$work_dir/example/fields.json" \
  --plan "$work_dir/example/fill_plan.json" \
  --source-root "$work_dir/example" --json
```

This produces a `PASS` result for one invented field. No real form or account is needed.

## Give an agent a checkable contract

Each map entry names a `field` and boolean `required` flag. Each planned value names its field, value and relative source path:

```json
{"field": "example_name", "value": "Example", "source": "reference.txt"}
```

The files contain arrays of entries. A blank awaiting a person uses `{"field":"example_name","status":"NEEDS_FOUNDER_INPUT"}`; the historical status name is retained by the API. A reference resolving inside the source root proves presence, not that its contents support the value.

The optional `docintegrity-stop` command adds explicit discovery roots, sidecar matching, audit reports, append-only summary events, lock handling and caller-supplied filing-evidence matching. It installs nothing automatically. [Configure the stop hook →](docs/stop-hook.md)

## Scope and development

The verifier handles saved PDF AcroForm values. It does not perform OCR, inspect flattened or scanned form contents, validate digital signatures, or compare the visible page with the form data. Review the document and its sources before relying on them.

```sh
python -m pip install '.[test]'
python -m unittest -v test_integrity
```

The tests generate temporary, invented documents. Local and hosted verification are recorded separately in [validation notes](docs/validation.md). See [contributing](CONTRIBUTING.md) for small, reproducible reports, and [provenance](PROVENANCE.md) for the source adaptations.

Original project material is offered under the [MIT license](LICENSE). Separately installed libraries retain their own terms. This repository is the Document Integrity helper; it does not include ArcturionDocuments' private filing system or a live ATLAS connection.
