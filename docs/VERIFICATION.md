# Source-copy verification — 2026-11-05

This report describes local checks of the prepared source copy, not a public
release certification or a GitHub-hosted Actions result.

## Executed

- Created a separate Python 3.13 environment on Windows, without system-site
  packages or the original application's virtual environment.
- Installed core and test dependencies from PyPI using `requirements-dev.txt`.
  Installation succeeded. The 37 resolved packages are recorded separately in
  `requirements-windows-py313.lock.txt`; Setup and CI use that snapshot.
- `pip check` found no broken requirements in the new environment.
- The curated suite passed **161 unittest cases in 24.251 seconds** in that
  environment. The optional Node test ran rather than being skipped.
- The **10 isolated legacy scripts** and the separate JSON-protocol regression
  script passed. These are reported separately from the unittest count.
- Tests include Tk integration with temporary storage, Stop/cancellation, Undo,
  manual extraction, retrieval boundaries, app-check evidence, publication
  scanning, Git ignore/forced-staging behavior and source-archive safety.
- Spanish-voice selection uses synthetic metadata; no voice executable or
  downloaded neural voice is required by that test.
- The publication checker found zero issues in the exported source. A separate
  local comparison found no exported occurrences of the known connection
  credentials or the local account name. This was a targeted supplement to
  pattern scanning, not proof that every possible secret format is recognized.
- Original application source, settings and local-data files matched their
  recorded pre-preparation hashes. The working application was not closed or
  restarted; no data migration or original-environment package install occurred.
- GitHub Action references were resolved against official action repositories
  and fixed to full commit SHAs. The workflow uses read-only permissions,
  GitHub-hosted Windows runners and no configured secrets/deployment steps.

## Not established by these checks

Actual inference latency, microphone capture, audible voice/accent quality,
optional Melo/Piper installation, real OCR language packs, real browser or SSH
operations, and unattended correctness of arbitrary generated applications were
not validated by the offline suite. Those features need separate opt-in tests
and explicit results. Model weights and external executables are not included.

At source-preparation time the GitHub workflow had **not run on GitHub**. This
historical local report does not certify later commits. Consult the repository's
[Actions history](https://github.com/Linkworld05/Nova/actions/workflows/tests.yml)
for the actual hosted results of each published commit.
Future dependency or code changes require fresh verification. See `SECURITY.md`
for the remaining security boundaries and `docs/PUBLISHING.md` for release steps.
