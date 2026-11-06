# Contributing

Thanks for improving Nova. This is an alpha desktop project. Prefer small,
reviewable changes with concrete behavior and honest verification results.

## Development

Use a separate checkout and synthetic data, not a working personal installation.
Install Python 3.13, run `Setup.cmd`, then install `requirements-dev.txt`.
Optional tools have separate requirements files. The runtime data is created
beside the source and is ignored by Git; do not add it with `git add -f`.

Before submitting:

```powershell
.venv\Scripts\python.exe tools\run_tests.py
.venv\Scripts\python.exe tools\check_public_tree.py
git diff --check
git diff --cached --stat
```

With no Git repository the privacy checker examines the source tree. In a Git
checkout it examines tracked files; pass `--all-files` to also inspect untracked
files before staging. It never prints matching secret values. Review the actual
staged diff yourself; automated scans cannot establish that everything is safe.

## Pull requests

- Explain the problem, resulting behavior and tests you ran.
- Add meaningful tests for fixes. Preserve existing cases and acceptance intent.
- Keep Tk mutations on the GUI thread. Test timers with controlled clocks/callbacks.
- Preserve command approvals, Stop and Undo. Do not infer success from an exit
  code or a visible process alone.
- Do not include model weights, executables, user manuals, logs, screenshots of
  private conversations, credentials, private server addresses or machine paths.
- State which hardware, audio or browser behavior remains unverified.
- By contributing original code you agree to license it under AGPL-3.0-or-later;
  identify third-party material and preserve its notices. Do not submit code or
  data you cannot distribute under the stated terms.

There is no CLA or automatic merge process configured. A maintainer reviews PRs.
Open an issue or discussion before a large dependency, architecture or license
change. Report security problems according to `SECURITY.md`, not a public issue
containing sensitive data.
