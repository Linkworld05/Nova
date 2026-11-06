# Changes limited to the public copy

The original local installation was preserved. This separate source copy:

- Exports an explicit set of app modules and synthetic regression tests.
- Replaces personal docs, configuration and screenshots with new generic docs
  and empty examples. Does not copy databases, models, audio or backups.
- Removes a machine-specific Start-menu fallback and startup log path.
- Defaults PC control and spoken replies to off; retains command approval.
- Rejects unknown SSH host keys instead of automatically trusting them.
- Adds a visible About & license entry with copyright, warranty and license text.
- Makes Spanish voice-selection tests use synthetic metadata without voice files.
- Keeps only synthetic scheduling fixtures from the old live benchmark helper.
- Supplies separate core/optional requirements, model-download helpers, offline
  regression runner, privacy checks and GitHub community/CI configuration.
- Preserves the MIT voice runtime and upstream pronunciation lexicon notices;
  excludes its generated examples and serialized cache.

No automatic updater, deployment, remote repository or user-data migration was
added. The verification report in `docs/VERIFICATION.md` distinguishes tests
performed locally from checks that still require real hardware or a GitHub run.
