# Privacy and publishing boundaries

This checkout starts without personal data. Running Nova creates local files:

| Local files/directories | Contents |
| --- | --- |
| `memory.sqlite3` | Conversations, chat turns, profile, reminders and facts |
| `manuals.sqlite3`, `manuals/` | Manual metadata and imported originals |
| `nova-agent-state.sqlite3`, `nova-quality.sqlite3` | Agent context, checkpoints, app-check evidence |
| `nova-knowledge-index.sqlite3`, `nova-metrics.sqlite3` | Retrieval index and local timing metrics |
| `*-settings.json`, `nova-runtime.json`, `pc-actions.json` | Device choices, models, commands and optional connection details |
| `recordings/`, `voice-questions/`, `wake-listen/` | Saved or temporary audio |
| `backups/`, `.nova-backups/`, `.nova-qa/` | Recovery copies, Undo journals and check reports |
| `app.log` | Diagnostic output that may contain paths or error context |

These are not encrypted. Use account/file permissions and private backups.
Deleting a chat does not necessarily erase all original recordings, manuals,
backups or checkpoints. Inspect them separately if you need data deletion.

`.gitignore` prevents ordinary staging of runtime files, but cannot remove a file
already committed or prevent `git add -f`. The public-tree checker uses tracked
files in Git mode and scans the source tree outside Git. Run `--all-files` before
the first staging step. Never upload a whole personal installation as a ZIP.

Only synthetic fixtures belong in tests. Do not attach real manuals or copied
conversations to an issue. For a useful bug report, reproduce it in a new private
folder and write a minimal fictional example with the same behavior.

Ollama inference is local. Model downloads, public-information web search,
website import and configured SSH/browser tools can use the network. An external
search query can contain the question text. Check model/package first-use
downloads before operating in a fully disconnected environment.

The shipped scanner recognizes common credential and private-path patterns. It
does not prove the absence of all personal information, credentials, copyrighted
content or malicious source. Maintainers must review the staged diff and any
distributed archive. A newly generated conversation can be private even when it
contains no token-shaped string.
