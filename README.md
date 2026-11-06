# Nova Local Assistant

**Alpha · Windows 10/11 x64 · Python 3.13 · local Ollama models**

[Español](README.es.md) · [Setup](docs/SETUP.md) · [Architecture](docs/ARCHITECTURE.md) · [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

Repository: [Linkworld05/Nova](https://github.com/Linkworld05/Nova) ·
[GitHub Actions](https://github.com/Linkworld05/Nova/actions/workflows/tests.yml)

Nova is a desktop application that uses local AI models to chat, work with
images and saved manuals, manage reminders, and edit projects through a coding
agent. Conversation history and imported documents stay on the local computer.
Web lookup, website imports, optional SSH tools and initial model downloads use
the network. Nova is not affiliated with OpenAI, GitHub or the model vendors.

## What works

- Text chat, picture attachments and clipboard images; vision uses a local model.
- Persistent chat history, manual imports (PDF, DOCX, text and public websites),
  citations and optional multilingual semantic retrieval.
- Optional microphone transcription, Windows speech, neural speech and wake word.
- Coding tools with command approval, checkpoints, Stop and file Undo.
- Requirement tracking, behavior-test execution, bounded corrections and a
  separate local-model acceptance review before claiming an app is verified.
- CPU-verified arithmetic and bounded scheduling tools.

The coding agent remains experimental. A window opening does not prove the
requested behavior works. Unsupported browser, visual, audible and device
checks remain unverified. Python audit hooks and temporary copies are not an OS
security sandbox. Review generated commands and use disposable projects first.

## Start with text chat

1. Install Python 3.13 x64 and [Ollama](https://ollama.com/download/windows).
2. Run `Setup.cmd` in a user-writable checkout. This installs the core dependencies
   in `.venv`; it does not download AI models or enable recording.
3. Download the default chat model:

   ```powershell
   ollama pull qwen3:14b
   ```

4. Start Ollama, then run `Start Assistant.cmd`.

For a smaller model, pull `qwen3:8b` and select **Fast** in **Settings & memory →
Performance**. Balanced uses 14B for chat and coding. Deep uses the larger
`qwen3-coder:30b` for coding and can be substantially slower when it spills into
system memory. No GPU is required by the offline test suite.

Speech playback, wake listening and PC control default to **off** in this source
distribution. Enable only the functions you want. See [setup](docs/SETUP.md) for
vision, offline speech models, OCR and optional browser/SSH dependencies.

## Run the tests

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe tools\run_tests.py
.venv\Scripts\python.exe tools\check_public_tree.py
```

The explicit suite uses synthetic data, local HTTP fixtures and temporary
projects. It does not call a real AI model or access a real microphone, VPS or
personal database. Install Node.js 24 to exercise the optional Node runner;
otherwise that case is reported as skipped. Do not use unrestricted unittest
discovery: some root scripts are legacy executable checks, run separately by the
curated runner. [Testing details](docs/TESTING.md).

## Data and configuration

This source-only repository ships **no user settings, credentials, databases,
recordings, imported manuals or model weights**. Nova creates runtime data in the
checkout folder. Keep the checkout in a private, writable location; it is not
an encrypted data store. `.gitignore` excludes generated data. Use only the
blank templates under `examples/`; never commit your populated configuration.
The public-tree checker is a supplementary safeguard, not a complete secrets
or copyright audit. See [privacy](docs/PRIVACY.md).

## Help improve Nova

Use Issues for reproducible defects and feature requests, Discussions for ideas,
and pull requests for code. Maintainers review proposed changes; contributing
does not automatically change anyone's installed app or train an AI model.
Start with the [roadmap](docs/ROADMAP.md) and [contribution guide](CONTRIBUTING.md).

## License

Nova's original code and artwork: **AGPL-3.0-or-later**, without warranty.
See [LICENSE](LICENSE), [NOTICE](NOTICE) and
[third-party notices](THIRD_PARTY_NOTICES.md). The optional vendored Melo runtime
retains its MIT license; its pronunciation lexicon has a separate notice.
Model weights and external executables have their own licenses and are not
redistributed here. PyMuPDF is used under its AGPL distribution terms.
