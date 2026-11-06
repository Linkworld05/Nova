# SPDX-License-Identifier: AGPL-3.0-or-later
"""MeloTTS neural speech engine (local, CPU-only, offline after first model download)."""
import threading

VOICES = [
    {"name": "MeloTTS · English (US)", "key": "EN-US", "culture": "en-US", "language": "EN"},
    {"name": "MeloTTS · English (British)", "key": "EN-BR", "culture": "en-GB", "language": "EN"},
    {"name": "MeloTTS · Spanish", "key": "ES", "culture": "es-ES", "language": "ES"},
]

_lock = threading.Lock()
_tts = {}


def available():
    try:
        import melo.api  # noqa: F401
        return True
    except Exception:
        return False


def _engine(language):
    with _lock:
        if language not in _tts:
            from melo.api import TTS
            _tts[language] = TTS(language=language, device="cpu")
        return _tts[language]


def find_voice(name):
    for voice in VOICES:
        if voice["name"] == name or voice["key"] == name:
            return voice
    return VOICES[0]


def synth(text, voice_name, wav_path):
    """Synthesize text to a WAV file with the named MeloTTS voice."""
    voice = find_voice(voice_name)
    tts = _engine(voice["language"])
    speakers = tts.hps.data.spk2id
    speaker = voice["key"] if voice["key"] in speakers else next(iter(speakers))
    tts.tts_to_file(text, speakers[speaker], output_path=str(wav_path))
