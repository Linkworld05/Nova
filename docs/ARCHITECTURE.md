# Architecture

Nova is currently a source-based Windows/Tk application, not a packaged service.
Its flat module layout is retained to make this export easy to review against
the working implementation. Refactoring should preserve the existing tests.

| Area | Modules | Responsibility |
| --- | --- | --- |
| Desktop | `launch.py`, `assistant.py`, `ui_theme.py`, `public_info.py` | Single instance, Tk chat UI, routing, legal notices |
| Inference | `model_runtime.py`, `picture_analysis.py` | Local Ollama requests, profiles, bounded calls, vision |
| Memory | `chat_history.py`, `agent_state.py`, `grounding.py` | Per-chat history, agent checkpoints, evidence handling |
| Manuals | `manual_library.py`, `website_manual.py`, `manual_ocr.py`, `semantic_index.py`, `manual_ui.py` | Import, local originals, page references, keyword/embedding retrieval |
| Audio | `voice_ui.py`, `local_audio.py`, `computer_audio.py`, `melo_speech.py`, `piper_speech.py` | Explicit recording, transcription, speech, wake listener |
| Agent | `codex_agent.py`, `agent_protocol.py`, `codex_controls.py` | Typed tools, work-folder boundaries, approvals, Stop, Undo |
| App checks | `app_quality.py`, `app_test_runner.py`, `app_test_worker.py`, `app_diagnostics.py`, `app_quality_ui.py` | Requirements, copied-source tests, corrections, review, evidence |
| CPU tools | `verified_tools.py`, `verified_reasoning.py` | Checked arithmetic and bounded schedules |
| Automation | `pc_actions.py`, `software_tools.py`, `reminders.py` | Explicit PC actions, local software configuration and reminders |

Generated code and imported documents are untrusted data. Tool permissions and
acceptance evidence must not be replaced by instructions embedded in them.
Temporary test copies reduce accidental writes; they do not provide OS isolation.

All Tk changes belong on the UI thread. Worker threads communicate through queues
and scheduled callbacks. GUI app launch, lifetime management, behavior tests and
visual inspection are separate concerns; a detached launch is not a verified app.

## Data flow

User input → routing → local model or deterministic tool → response/evidence →
per-chat local storage. Web queries and website import are explicit external
network paths; SSH/browser tools are optional. Model downloads are separate from
normal local inference. There is no project-managed remote analytics backend.

## Refactoring targets

Separate orchestration from UI in `assistant.py`, split command adapters from the
agent loop, and make app-check phases easier to reason about. Keep durable facts
separate from transient failures. Do not remove safeguards just to satisfy a
mocked happy-path test.
