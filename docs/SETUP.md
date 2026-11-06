# Setup from a clean Windows checkout

## Core

`Setup.cmd` uses `requirements-windows-py313.lock.txt`, the exact core/offline-test
dependency versions verified for this source copy. The bounded `requirements.txt`
and `requirements-dev.txt` are inputs for future upgrades. Regenerate the lock only
in a new core environment, then run all checks; never freeze a personal or optional-
voice environment into the public repository. CI uses the same Windows/Python lock.

Install Python 3.13 x64 with Tk support and Ollama for Windows. Use a private,
user-writable directory, not Program Files or a shared/network folder. Run
`Setup.cmd`. The core installs no AI model and no GPU PyTorch build. Nova's LLM
inference is handled by Ollama; a CPU can run it too, with different latency.

```powershell
.venv\Scripts\python.exe tools\download_models.py --profile Balanced
```

This explicitly calls `ollama pull` for the profile's chat/coding models. Start
Ollama first. Fast selects 8B chat and 14B coding; Balanced selects 14B for both;
Deep selects 14B chat and a 30B coding model. Select the same profile inside Nova.
The helper downloads models but does not change a running app or its settings.
Start with Fast on limited VRAM. Models that do not fit can use system memory
and be much slower; no universal latency or memory claim is made.

Launch with `Start Assistant.cmd`, or use `.venv\Scripts\python.exe launch.py`
from a terminal for diagnostics. Inspect `app.log` locally if startup fails.
Do not upload the log without checking it for private content.

## Pictures and semantic manual retrieval

```powershell
.venv\Scripts\python.exe tools\download_models.py --vision --embeddings
```

The current defaults are `gemma3:4b` for vision and `embeddinggemma` for manual
embeddings. Text/manual keyword retrieval works without the embedding model.
Build the optional manual index through Settings & memory → Performance.
Models downloaded by Ollama live in its own managed storage, not in Git.

## Optional microphone transcription

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-voice.txt
.venv\Scripts\python.exe tools\download_models.py --whisper
```

The explicit Whisper download populates `models/` with the public
`Systran/faster-whisper-small` cache. The app subsequently opens it in local-only
mode. Its optional Parakeet backend is not installed by this core setup; if absent
the app falls back to Whisper. Select your own microphone in Record & talk.
No input/output device name or recording is preconfigured in the repository.

## Speech playback

Windows-installed System.Speech voices are the standard fallback and require
no bundled neural model. Playback starts disabled. The availability and accent
depend on the voices installed in your Windows account.

Piper is optional: place the separately acquired Windows runtime under
`models/piper/` and compatible `.onnx` plus `.onnx.json` voice files under
`models/piper/voices/`. Review the exact runtime and voice model licenses first.
Mexican Spanish is selected automatically only when a compatible voice is
installed; this source repository contains no voice files.

The original adapted Melo runtime is included as source under its MIT license:

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-melo.txt
```

Melo is an advanced optional extra with its original strict dependency pins. It
can require eSpeak-NG, language resources and first-use Hugging Face downloads.
The core clean-environment verification does not establish Melo installation or
audible voice quality. Use Windows speech if this optional stack is unavailable.

## Optional OCR, browser and SSH

Install `requirements-ocr.txt` for Windows OCR and enable a Windows recognition
language. Searchable PDFs, Word and text do not require this extra.

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-tools.txt
.venv\Scripts\python.exe -m playwright install chromium
```

SSH additionally requires a trusted entry in your account's `~/.ssh/known_hosts`.
Verify the server fingerprint through a trusted channel before accepting it with
your SSH client. Nova rejects unknown host keys. Enter your own VPS details only
in your local settings, never in a commit. No real remote connection is required
by the repository tests.

## Configuration and upgrades

Optional empty templates live under `examples/`. They are documentation, not
active configuration. Nova creates its settings and SQLite files beside source.
Do not copy an existing installation's settings or databases into a public repo.
Back up private runtime files separately before updating source. There is no
automatic update, deployment or migration service configured by this repository.

Compatibility variables `OMI_MODEL`, `OMI_CODEX_MODEL` and `OMI_VISION_MODEL`
override local model choices. Set them in your environment, not in committed
configuration. Disabling command approvals is a local preference with real host
effects; keep them enabled for untrusted work.
