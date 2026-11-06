# SPDX-License-Identifier: AGPL-3.0-or-later
"""Configure installed software through config files, the registry, or keystrokes.

This is the "edit any software" toolkit. Three mechanisms, safest first:
  1. Config files (INI/JSON/XML) in well-known locations, edited with a backup.
  2. Windows registry (HKCU/HKLM), exported for backup before any write.
  3. Keystrokes typed into a matched window (text only; no blind mouse clicks).

Safety rails by design:
  - File edits are allowed ONLY on paths returned by find_configs (never arbitrary paths).
  - A timestamped backup copy is made before every file or registry change.
  - Everything applied is appended to backups/software/configure.log.
  - Registry writes are limited to HKCU and HKLM.
  - Keystrokes are plain text (SendKeys specials escaped); target window must exist.
"""
import configparser
import json
import os
import re
import shutil
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKUP_ROOT = ROOT / "backups" / "software"

def _env_dir(name, fallback):
    value = os.environ.get(name)
    return Path(value) if value else Path(fallback)


SEARCH_ROOTS = [
    _env_dir("APPDATA", Path(os.environ.get("USERPROFILE", r"C:\Users")) / "AppData" / "Roaming"),
    _env_dir("LOCALAPPDATA", Path(os.environ.get("USERPROFILE", r"C:\Users")) / "AppData" / "Local"),
    _env_dir("PROGRAMDATA", r"C:\ProgramData"),
    Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Documents",
]
CONFIG_EXTS = {".ini", ".cfg", ".conf", ".json", ".xml", ".yaml", ".yml", ".toml", ".properties"}
MAX_READ = 200_000
SNIPPET = 4000
SKIP_DIRS = {"node_modules", ".venv", ".git", "Cache", "Code Cache", "GPUCache",
             "Crashpad", "__pycache__", "Temp", "tmp", "Logs", "log"}


def _log(line):
    try:
        BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
        with open(BACKUP_ROOT / "configure.log", "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + line + "\n")
    except Exception:
        pass


def _tokens(app):
    return [t for t in re.split(r"[^a-z0-9]+", (app or "").lower()) if len(t) >= 3]


def find_configs(app, deadline_s=10):
    """Locate config files that plausibly belong to the named app."""
    toks = _tokens(app)
    if not toks:
        raise ValueError("Say which program you want to configure.")
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower())
    scored = {}
    deadline = time.monotonic() + deadline_s

    def scan(folder, max_depth):
        base = Path(folder)
        base_hay = norm(base.name)
        for dirpath, dirnames, filenames in os.walk(base):
            if time.monotonic() > deadline:
                dirnames[:] = []
                return
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            try:
                rel = Path(dirpath).relative_to(base)
                depth = len(rel.parts)
            except ValueError:
                rel, depth = Path(), 0
            if depth >= max_depth:
                dirnames[:] = []
            parent = norm(Path(dirpath).name)
            ancestors = base_hay + " " + " ".join(norm(p) for p in rel.parts[:-1])
            for fn in filenames:
                if Path(fn).suffix.lower() not in CONFIG_EXTS:
                    continue
                stem = norm(Path(fn).stem)
                haystack = stem + " " + parent + " " + ancestors
                if not all(t in haystack for t in toks):
                    continue
                score = (3 if any(t in stem for t in toks) else 0) + \
                        (2 if any(t in (parent + " " + ancestors) for t in toks) else 0)
                if score:
                    p = Path(dirpath) / fn
                    key = os.path.realpath(p).lower()  # dedupe junction duplicates
                    scored[key] = (max(scored[key][0], score) if key in scored else score, p)

    roots = [b for b in SEARCH_ROOTS if str(b) and b.is_absolute() and b.is_dir()]
    # Pass 1: folders NAMED like the app (catches %LOCALAPPDATA%\AppName\settings.ini fast).
    for base in roots:
        if time.monotonic() > deadline:
            break
        try:
            hits = [e for e in base.iterdir()
                    if e.is_dir() and any(t in norm(e.name) for t in toks)]
        except OSError:
            continue
        for d in hits:
            if time.monotonic() > deadline:
                break
            scan(d, 4)
    # Pass 2: broad shallow sweep with the remaining time.
    for base in roots:
        if time.monotonic() > deadline:
            break
        scan(base, 3)
    top = sorted(scored.values(), key=lambda v: (-v[0], len(str(v[1]))))[:6]
    return [p for _, p in top]


def _detect_encoding(path):
    """Return 'utf-16' for UTF-16 files (with or without BOM), else 'utf-8-sig'."""
    try:
        raw = path.read_bytes()[:8192]
    except Exception:
        return "utf-8-sig"
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return "utf-16"
    if raw and raw.count(b"\x00") > len(raw) // 4:
        return "utf-16"
    return "utf-8-sig"


def read_snippet(path):
    """Return (kind, text) for a config file, truncated for the decider."""
    ext = path.suffix.lower()
    kind = {".json": "json", ".xml": "xml"}.get(ext, "ini-like")
    enc = _detect_encoding(path)
    try:
        text = path.read_text(encoding=enc, errors="replace")
    except Exception as e:
        return (kind, f"<unreadable: {e}>")
    if len(text) > SNIPPET:
        text = text[:SNIPPET] + f"\n... <truncated, {len(text)} bytes total>"
    return (kind, text)


def _backup(path):
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = BACKUP_ROOT / f"{stamp}--{path.name}"
    n = 1
    while dest.exists():
        dest = BACKUP_ROOT / f"{stamp}-{n}--{path.name}"
        n += 1
    shutil.copy2(path, dest)
    return dest


def _set_ini(path, section, key, value):
    if not path.is_file():
        raise ValueError(f"{path.name} not found.")
    enc = _detect_encoding(path)
    cp = configparser.RawConfigParser(strict=False)
    cp.optionxform = str  # preserve key case
    cp.read(path, encoding=enc)
    if section and not cp.has_section(section):
        raise ValueError(f"Section [{section}] not found in {path.name}.")
    target = cp[section] if section else cp.defaults()
    old = target.get(key)
    if not section:
        cp.defaults()[key] = value
    else:
        cp.set(section, key, value)
    bak = _backup(path)
    with open(path, "w", encoding=enc) as f:
        cp.write(f)
    _log(f"INI {path} [{section}] {key}: {old!r} -> {value!r} (backup {bak.name})")
    return f"{path.name}: set [{section or 'DEFAULT'}] {key} = {value} (was {old!r}; backup saved)."


def _set_json(path, dotted, value):
    if not path.is_file():
        raise ValueError(f"{path.name} not found.")
    enc = _detect_encoding(path)
    data = json.loads(path.read_text(encoding=enc))
    cur = data
    parts = [p for p in dotted.split(".") if p]
    for p in parts[:-1]:
        cur = cur.setdefault(p, {})
        if not isinstance(cur, dict):
            raise ValueError(f"{dotted} is not a JSON object path.")
    old = cur.get(parts[-1]) if isinstance(cur, dict) else None
    cur[parts[-1]] = value
    bak = _backup(path)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                    encoding="utf-8" if enc == "utf-8-sig" else enc)
    _log(f"JSON {path} {dotted}: {old!r} -> {value!r} (backup {bak.name})")
    return f"{path.name}: set {dotted} = {value!r} (was {old!r}; backup saved)."


def _set_xml(path, tagpath, attribute, value):
    if not path.is_file():
        raise ValueError(f"{path.name} not found.")
    tree = ET.parse(path)
    node = tree.getroot()
    for tag in [t for t in tagpath.split("/") if t]:
        found = node.find(tag)
        if found is None:
            raise ValueError(f"Tag '{tag}' not found under {node.tag} in {path.name}.")
        node = found
    if attribute:
        old = node.get(attribute)
        node.set(attribute, str(value))
        what = f"{node.tag}[@{attribute}]"
    else:
        old = node.text
        node.text = str(value)
        what = f"{node.tag} text"
    bak = _backup(path)
    enc = _detect_encoding(path)
    tree.write(path, encoding="utf-8" if enc == "utf-8-sig" else enc, xml_declaration=True)
    _log(f"XML {path} {what}: {old!r} -> {value!r} (backup {bak.name})")
    return f"{path.name}: set {what} = {value!r} (was {old!r}; backup saved)."


def _reg_backup(regpath):
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", regpath)[-80:]
    dest = BACKUP_ROOT / f"{stamp}--{safe}.reg"
    exists = subprocess.run(["reg.exe", "query", regpath],
                            capture_output=True, timeout=30,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode == 0
    if not exists:
        # Nothing to export: the key will be created by this change, so the undo
        # is deleting it. Record that in the backup marker.
        dest.write_text(f"; registry key did not exist before this change\r\n"
                        f"; undo: reg delete \"{regpath}\" /f\r\n", encoding="ascii")
        return dest
    p = subprocess.run(["reg.exe", "export", regpath, str(dest), "/y"],
                         capture_output=True, timeout=30,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        raise ValueError("Could not back up registry key " + regpath)
    return dest


_REG_TYPE = {"string": "REG_SZ", "expandstring": "REG_EXPAND_SZ", "dword": "REG_DWORD",
             "qword": "REG_QWORD", "binary": "REG_BINARY", "multistring": "REG_MULTI_SZ"}


def _set_reg(regpath, name, value, rtype="string"):
    """Write a registry value via reg.exe with an argument list — no shell and no
    string interpolation, so arbitrary text in name/value cannot inject commands.
    (New-ItemProperty is unusable on recent Windows builds: provider regression.)"""
    regpath = str(regpath or "").strip()
    if not re.match(r"^(HKCU|HKLM)\\", regpath, re.I):
        raise ValueError("Registry path must start with HKCU\\ or HKLM\\.")
    regpath = "HKCU\\" + regpath[4:].lstrip("\\") if regpath.upper().startswith("HKCU") else "HKLM\\" + regpath[4:].lstrip("\\")
    rtype = (rtype or "string").lower()
    reg_type = _REG_TYPE.get(rtype)
    if not reg_type:
        raise ValueError("Unknown registry value type " + rtype)
    name = str(name or "").strip()
    if not name:
        raise ValueError("Registry value name is empty.")
    value = str(value)
    if "\n" in value or "\r" in value:
        raise ValueError("Text with line breaks is not allowed in a registry setting.")
    if rtype in ("dword", "qword"):
        try:
            value = str(int(value.strip(), 0))
        except ValueError:
            raise ValueError(f"{rtype} values must be whole numbers (got {value!r}).")
    bak = _reg_backup(regpath)
    p = subprocess.run(["reg.exe", "add", regpath, "/v", name, "/t", reg_type, "/d", value, "/f"],
                       capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        detail = (p.stdout + p.stderr).decode(errors="replace")
        raise ValueError("Registry write failed: " + " ".join(detail.split())[:160])
    _log(f"REG {regpath} {name} ({rtype}) = {value!r} (backup {bak.name})")
    return f"Registry {regpath}\\{name} set to {value!r} (backup saved)."


def _sendkeys_escape(text):
    out = []
    for ch in text:
        if ch in "+^%~()[]{}":
            out.append("{" + ch + "}")
        elif ch == "\n":
            out.append("{ENTER}")
        else:
            out.append(ch)
    return "".join(out)


def type_in_window(title, text, press_enter=False):
    """Activate a window by title and type plain text into it (text only)."""
    title = (title or "").strip()
    if not title:
        raise ValueError("Say which window to type into.")
    if not text:
        raise ValueError("There is no text to type.")
    keys = _sendkeys_escape(text) + ("{ENTER}" if press_enter else "")
    script = (f"$w=New-Object -ComObject WScript.Shell; "
              f"if (-not $w.AppActivate('{title.replace(chr(39), chr(39)*2)}')) {{ exit 1 }}; "
              f"Start-Sleep -Milliseconds 300; $w.SendKeys(\"{keys.replace(chr(34), chr(34)*2)}\")")
    encoded = __import__("base64").b64encode(script.encode("utf-16-le")).decode("ascii")
    p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                       capture_output=True, timeout=30,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if p.returncode != 0:
        raise ValueError(f"No window titled like '{title}' is open. Open it first, then tell me to type.")
    _log(f"TYPE window~'{title}' text={text!r} enter={press_enter}")
    return f"Typed into the {title} window."


def configure(app, goal, decider):
    """Full pipeline: discover configs -> model decides minimal ops -> apply with backups.

    decider(app, goal, candidates) -> dict with key "ops" (list) or "none" reason.
    candidates: list of {path, name, kind, snippet}.
    """
    found = find_configs(app)
    if not found:
        return (f"I looked for configuration files of '{app}' in the standard locations "
                "(AppData, ProgramData, Documents) and found none. It may store settings in "
                "the registry only, or need its own interface — tell me the setting you want "
                "and I can try the registry path if you know it, or walk you through the clicks.")
    candidates = []
    for p in found:
        kind, snippet = read_snippet(p)
        candidates.append({"name": p.name, "path": str(p), "kind": kind, "snippet": snippet})
    try:
        plan = decider(app, goal, candidates)
    except Exception as e:
        return f"I found {len(candidates)} config file(s) for {app}, but could not decide what to change: {e}"
    if not isinstance(plan, dict) or not plan.get("ops"):
        reason = (plan or {}).get("reason") or "no safe change matched the goal"
        return (f"I found {len(candidates)} config file(s) for {app}, but I don't see a setting I can "
                f"safely change for that ({reason}). If you know the exact option name, tell me and I'll set it.")
    by_name = {c["name"]: Path(c["path"]) for c in candidates}
    results = []
    allowed = {str(p.resolve()).lower() for p in by_name.values()}
    for op in plan["ops"]:
        try:
            kind = str(op.get("kind", "")).strip().lower()
            if kind == "set_reg":
                results.append(_set_reg(op.get("path"), op.get("name"), op.get("value"), op.get("type", "string")))
                continue
            name = str(op.get("file", ""))
            path = by_name.get(name)
            if path is None or str(path.resolve()).lower() not in allowed:
                raise ValueError(f"'{name}' is not one of the discovered config files; refusing.")
            if kind == "set_ini":
                results.append(_set_ini(path, str(op.get("section", "")).strip(), str(op.get("key", "")), op.get("value")))
            elif kind == "set_json":
                results.append(_set_json(path, str(op.get("key", "")), op.get("value")))
            elif kind == "set_xml":
                results.append(_set_xml(path, str(op.get("path", "")), str(op.get("attribute", "")), op.get("value")))
            else:
                raise ValueError("Unknown op kind " + kind)
        except Exception as e:
            results.append(f"Skipped one change ({e})")
    if not results:
        return "Nothing was changed."
    note = " If the program is running, restart it to pick up the new settings."
    return "Done. " + " ".join(results) + note
