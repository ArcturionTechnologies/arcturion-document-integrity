# Contributing

Start with an invented, minimal form. Reproduce a problem using the smallest field map and fill plan that demonstrates it, and include the command, exit code, Python version and dependency versions.

Do not upload personal forms, signatures, credentials, private source documents or unredacted audit output. Recreate the relevant field structure and values with fictional data instead. A report usually needs only the observed result and the expected comparison result.

Run the synthetic suite before proposing a change:

```sh
python -m pip install '.[test]'
python -m unittest -v test_integrity
```

Keep changes focused. A useful regression demonstrates a wrong comparison, an unintended write or an incorrect hook decision. Preserve explicit roots, refusal of unsafe output aliases, and the binding between audited PDF bytes and later evidence checks.

If your issue is specific to a PDF reader structure, describe how to generate an equivalent invented AcroForm. This project checks AcroForm data; OCR, visual-layout validation, real filing, signature verification and source-claim authentication are separate capabilities.
