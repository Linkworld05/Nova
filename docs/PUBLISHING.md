# Publishing this source copy

The public repository is `https://github.com/Linkworld05/Nova`. Keep the personal
installation separate; publish only reviewed source from this clean directory.
The initial source preparation was local; repository publication is a separate
step. These instructions also apply when preparing later contributions or forks.

1. Run the curated tests and `python tools/check_public_tree.py --all-files`.
2. Inspect `LICENSE`, `NOTICE`, dependency/model notices and every staged file.
3. Initialize Git in this clean directory if needed. Create a private repository
   for a final review, or a public alpha only after the source/data review.
4. Commit only reviewed source. Never copy private data into a private staging
   repo expecting to remove it later before making the repo public.
5. After pushing, check the real Actions result; local test results are not a
   GitHub-hosted CI run. Enable branch protection/required review after the first
   successful CI check. Do not grant workflows deployment credentials.
6. Enable Discussions, Issues and private vulnerability reporting. Review the
   default templates and contact channels before announcing the project.
7. Tag an alpha release only after clean-machine setup and any claimed hardware
   features have been tested. Use source archives; model downloads remain separate.

To build an archive locally, run `python tools/build_source_archive.py --output
../Nova-source.zip`. It rejects private/runtime files, writes outside the checkout,
checks ZIP integrity and refuses to replace an existing archive. In an initialized
Git repository it packages tracked files, so review the staged file list first.

Suggested contribution areas are in `docs/ROADMAP.md`. No CODEOWNERS file with an
invented maintainer identity is supplied. Add it once the actual repository owner
and review team are known. This source preparation does not prove that every
optional speech/browser/SSH stack installs or behaves correctly.
