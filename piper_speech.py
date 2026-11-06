# SPDX-License-Identifier: AGPL-3.0-or-later
"""Piper neural speech: local voices under models/piper/voices, fully offline."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PIPER_DIR = ROOT / "models" / "piper"
VOICES_DIR = PIPER_DIR / "voices"


def exe_path():
    path = PIPER_DIR / "piper.exe"
    return path if path.is_file() else None


def voice_list():
    """One entry per downloaded voice model: key, display name, language code."""
    result = []
    for cfg in sorted(VOICES_DIR.glob("*.onnx.json")):
        try:
            meta = json.loads(cfg.read_text(encoding="utf-8"))
        except ValueError:
            continue
        key = cfg.name[: -len(".onnx.json")]
        if not (VOICES_DIR/(key+".onnx")).is_file():continue
        lang = (meta.get("language") or {}).get("code", "")
        speaker = (meta.get("speaker") or {}).get("name", "")
        quality = meta.get("quality", "")
        if not speaker:
            # Inference configs carry no display metadata; derive from the key: en_US-lessac-medium
            parts = key.split("-")
            if len(parts) >= 3:
                lang, quality = parts[0], parts[-1]
                speaker = " ".join(w.capitalize() for w in "-".join(parts[1:-1]).split("_"))
            else:
                speaker, quality = key, ""
        name = f"{speaker} ({quality}) · {lang}" if quality else (f"{speaker} · {lang}" if lang else key)
        if lang=="es_MX":name=f"Piper · Español (México) · {speaker} ({quality})"
        elif lang=="es_ES":name=f"Piper · Español (España) · {speaker} ({quality})"
        result.append({"key": key, "name": name, "culture": lang})
    return result


def available():
    return exe_path() is not None and bool(voice_list())


def find_voice(name):
    """Match a display name or key; fall back to the first downloaded voice."""
    voices = voice_list()
    for v in voices:
        if v["name"] == name or v["key"] == name:
            return v
    return voices[0] if voices else None
