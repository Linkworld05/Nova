# Third-party components

Nova's original source and artwork use AGPL-3.0-or-later. This does not replace
the licenses or notices of dependencies, external tools, model weights or data.

| Component | Use | Terms / source |
| --- | --- | --- |
| PyMuPDF / MuPDF | PDF extraction and rendering | AGPL/commercial dual license; this distribution uses the AGPL option. [Official licensing](https://pymupdf.io/licensing) |
| `vendor/nova-melo-runtime` | Optional, locally adapted MeloTTS runtime | MIT, Copyright (c) 2024 MyShell.ai; full notice retained in its `LICENSE`. [Upstream](https://github.com/myshell-ai/MeloTTS) |
| `vendor/nova-melo-runtime/melo/text/cmudict.rep` | Optional pronunciation lexicon | Carnegie Mellon Pronouncing Dictionary, Copyright 1998 Carnegie Mellon University. Its original unrestricted-use/redistribution notice and acknowledgments are retained at the start of the file. Obtained from the MeloTTS upstream source. |
| Python, Tk, NumPy, SciPy, Pillow and other pip dependencies | Runtime and development | Separately installed under their respective distributions' license notices; not relicensed by Nova. |
| Ollama and AI models | Optional local inference | Separately installed. Review the runtime license and each selected model's model card. Model weights are not included. |
| Faster Whisper / CTranslate2 models | Optional transcription | Separately installed/downloaded. Check each package and model card. |
| Piper executables and voice models | Optional neural speech | Not bundled. Runtime variants and voice models can have different terms; use the license associated with the exact download. |
| eSpeak-NG | Optional phonemizer for neural speech | Separately installed; not bundled. |

The repository intentionally excludes serialized pronunciation caches, generated
phonemizer examples, downloaded model assets and all personal runtime data.
Any source changes to the vendored Melo runtime retain its MIT notice.

Dependencies may change their licenses over time. Before redistributing a binary
release, record the exact versions, include their notices and offer corresponding
source as required. The core requirements file is bounded rather than a complete
lock of one developer's computer. A separate Windows/Python 3.13 lock records only
the newly verified core/test environment. This source copy is not a commercial license
for PyMuPDF or any separately licensed component.
