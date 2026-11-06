# Testing

Use Python 3.13 on Windows. Install `requirements-dev.txt`. The standard suite is
an explicit module list in `tools/run_tests.py`; it intentionally avoids live
model, audio and personal installation checks. Some tests create hidden or
withdrawn Tk windows and require a desktop-capable Windows session. A display-
less Linux runner is not a substitute for these tests.

```powershell
python tools/run_tests.py
python tools/run_tests.py --core-only
python tools/check_public_tree.py
python tools/check_public_tree.py --all-files
```

The first command runs the curated unittest suite and each legacy script in an
isolated subprocess. Legacy checks are not silently counted as unittest cases.
Loopback HTTP fixtures and termination of owned child processes are required by
Stop/streaming tests. The runner directs temporary files to a temporary workspace.
It cleans that workspace and does not connect to Ollama or a real VPS.

The Spanish-voice tests use synthetic voice metadata, not a bundled executable or
audio model. Actual playback/accent quality needs a separate opt-in listening
test. Node.js 24 enables Node-runner coverage; absence is an explicit skip, not
proof that Node behavior works.

`test_public_distribution.py` tests privacy scanning, safe defaults, offline
model-download behavior and configuration portability. The public-tree check
blocks common private runtime paths, model/binary artifacts, symlinks, files over
20 MiB and known secret formats. It reports paths and categories, not secret text.

CI runs syntax checks, source-tree checks, core tests and legacy scripts on a
GitHub-hosted Windows runner. It has read-only repository access, no deployment
step and no configured secrets. A local pass does not mean the workflow has run
on GitHub; inspect the actual Actions result after publication.

Do not weaken tests, replace requested behavior with mocks, or call a skipped
physical check verified. Generated-app workers use copied source and bounded
execution but are not OS security sandboxes. Use a VM for unfamiliar code.
