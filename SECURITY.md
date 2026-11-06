# Security policy

Nova is alpha software. No release currently carries a long-term security
support guarantee. Use the current reviewed source; fixes are reviewed on the
default branch before a tagged release is made.

## Reporting

After repository publication, maintainers should enable GitHub private
vulnerability reporting. Use the repository's **Security → Report a vulnerability**
when available. If private reporting is not enabled, open a minimal issue asking
for a private contact channel without exploit details or sensitive attachments.
Do not post credentials, recordings, personal databases or identifiable logs.
No unverified email address or maintainer identity is supplied in this source.

## Boundaries and known limitations

- Local models and generated commands can be wrong. Keep command approvals on.
  Use disposable work folders or a VM for unfamiliar generated code.
- Python test workers restrict common writes, subprocesses and sockets, but are
  not an OS sandbox. Node tests and approved shell commands can affect the host.
- Stop cancels work owned by the current run. A deliberately detached GUI or
  service can outlive the command; do not treat it as managed/cancelled by Stop.
  Undo restores journaled file edits and does not reverse shell/remote effects.
- Settings, transcripts and checkpoints are local plaintext. Redaction is
  best-effort and does not replace secret storage or access controls.
- Web lookup sends a query to an external search provider; website imports
  contact the requested servers. Model packages may download on first use.
- Optional SSH uses verified known-host entries and rejects unknown keys in this
  distribution. Establish a trusted host entry before connecting; never bypass
  host-key checks to make a deployment appear successful.
- Do not expose the local Ollama endpoint to untrusted networks.

CI runs untrusted PR code on GitHub-hosted runners, with read-only repository
permissions, no configured secrets and no self-hosted runner. Review changes to
workflow files carefully. Never add deployment credentials to this test workflow.
