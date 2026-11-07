# Dependency notices

Original Arcturion project material uses the MIT terms in LICENSE. Those terms do not relicense separately installed libraries, the interpreter, or their bundled dependencies. No library or model is bundled in this source tree.

| Component | Use | Separate terms |
| --- | --- | --- |
| pypdf 6.10.0 | Runtime PDF AcroForm reading | [BSD-3-Clause](https://github.com/py-pdf/pypdf/blob/6.10.0/LICENSE) |
| PyMuPDF 1.27.2.2 | Optional invented fixture generator and tests | [AGPL or commercial](https://pypi.org/project/pymupdf/1.27.2.2/); [upstream explanation](https://pymupdf.readthedocs.io/en/latest/about.html#license-and-copyright) |
| Python | Separately installed interpreter | [Python license](https://docs.python.org/3/license.html) |
| setuptools | Source build tooling | Its own installed distribution license; not a runtime dependency |

The base installation needs pypdf only. Installing the examples or test extra adds PyMuPDF. Check the actual library distribution and the terms relevant to your use or redistribution; the project's MIT text does not replace them.

Dependencies are pinned to the versions used in the release checks. Updates should rerun the saved-value, malformed-input, protected-output and hook byte-identity cases before changing the pins.
