# Validation

The core at technical freeze 0679eeb2d823896271541edfa81f50f9e50b87aa passed 52 bundled synthetic cases. Independent checks also exercised malformed maps/plans, unsafe output aliases, discovery and lock handling, audit/filing byte replacement and output failure. The independent exercises overlap the bundled suite; they are not a larger count of unique product guarantees.

For release preparation, those same 52 cases passed on macOS with Python 3.11.15 and 3.14.7, pypdf 6.10.0 and optional PyMuPDF 1.27.2.2. They also passed against wheel-installed modules from outside the source checkout. Both installed commands showed help; the documented invented-form example passed; an intentionally wrong planned value produced verifier exit 2 and a hook block; an existing audit output produced exit 3. Installed package metadata included the MIT expression and separate dependency notices, and both environments passed `pip check`.

The release changes packaging, documentation, dependency pins, proposed license and hosted-check configuration. The reviewed verifier, hook, synthetic tests and fixture generator remain byte-identical to the core freeze. Package contents, local documentation links, outgoing source and bounded Git history are checked before publication. No scanner result is a guarantee that every sensitive value or defect has been detected.

Hosted checks are configured for Python 3.10, 3.11 and 3.14 on Ubuntu. Python 3.10 and hosted execution remain unverified until an actual run exists. The checks use invented temporary forms and do not install a host hook, access accounts, validate real filing, or inspect private forms.

Passing tests do not prove every PDF field structure is supported, that the visible form matches its stored values, that a source substantiates a value, or that concurrent filesystem races and power loss are comprehensively handled.
