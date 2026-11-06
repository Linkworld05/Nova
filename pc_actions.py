# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local PC control for the voice assistant.

Whitelist-only executor: no arbitrary shell commands, no file writes, no
network beyond opening a user-facing URL or Start Menu shortcut. Anything not
in ALLOWED is refused. Actions run on the PC the assistant is installed on.
"""
import base64
import ctypes
import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

ALLOWED = {"open_app", "open_url", "media", "volume", "lock", "custom", "find_file"}

# User folders searched by find_file. File contents are never read.
_SEARCH_ROOTS = ["Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"]


def find_file(term, open_it=False):
    """Search user folders for files whose name contains the term (max ~10s, top 5)."""
    import time
    query = (term or "").strip().lower()
    if len(query) < 2:
        raise ValueError("Say at least two letters of the file name.")
    words = [w for w in re.split(r"[\s\-_.,]+", query) if len(w) >= 2]
    matches = []
    deadline = time.monotonic() + 10
    home = Path.home()
    skip = {".venv", "node_modules", ".git", "AppData", "__pycache__"}
    for root_name in _SEARCH_ROOTS:
        if time.monotonic() > deadline: break
        base = home / root_name
        if not base.is_dir(): continue
        stack = [base]
        while stack and time.monotonic() <= deadline and len(matches) < 8:
            folder = stack.pop()
            try:
                for entry in folder.iterdir():
                    if entry.name in skip: continue
                    try:
                        if entry.is_dir(): stack.append(entry)
                        elif all(w in entry.name.lower() for w in words): matches.append(entry)
                    except OSError: continue
            except OSError: continue
    if not matches:
        return f"I couldn't find any file named like '{term}' in your Desktop, Documents, Downloads, Pictures, Music, or Videos."
    matches.sort(key=lambda p: (len(p.name), p.name))
    top = matches[:5]
    listing = "; ".join(f"{p.name} ({p.parent.name})" for p in top)
    if open_it:
        os.startfile(str(top[0]))
        return f"Found and opened {top[0].name} from {top[0].parent.name}. Also: " + "; ".join(p.name for p in top[1:]) if len(top) > 1 else f"Found and opened {top[0].name}."
    return "I found: " + listing + ". Say 'open it' to open the first one."

# User-defined voice commands. Edit pc-actions.json next to this file:
#   {"open downloads": {"command": "explorer %USERPROFILE%\\Downloads",
#                       "reply": "Opening Downloads.",
#                       "enabled": true}}
# Keys are spoken names (matched case-insensitively); "command" runs exactly
# as written via cmd. Nothing outside this file (and the built-ins above) can run.
CUSTOM_PATH = Path(__file__).resolve().parent / "pc-actions.json"


def custom_actions():
    """Spoken-name -> command entry, from the user's pc-actions.json."""
    try:
        data = json.loads(CUSTOM_PATH.read_text(encoding="utf-8-sig"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k).strip().lower(): v for k, v in data.items() if isinstance(v, dict) and v.get("command")}


def custom_descriptions():
    """One-line list of available custom commands for the classifier prompt."""
    items = []
    for name, entry in sorted(custom_actions().items()):
        if entry.get("enabled", True):
            items.append(f'- custom: field "name": exactly "{name}"')
    return items


def _find_custom(name):
    query = (name or "").strip().lower()
    if not query:
        raise ValueError("No custom command name was given.")
    customs = custom_actions()
    if query in customs:
        return query, customs[query]
    for key in sorted(customs):
        if key.startswith(query):
            return key, customs[key]
    raise ValueError("No custom command named '" + str(name) + "'. Add it to pc-actions.json.")

# Windows virtual-key codes for multimedia keys (keybd_event).
VK = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "mute": 0xAD, "down": 0xAE, "up": 0xAF}

_MEDIA_RESULT = {"play_pause": "Toggled play/pause.", "next": "Skipped to next.", "previous": "Back to previous."}
_VOLUME_RESULT = {"up": "Volume up.", "down": "Volume down.", "mute": "Toggled mute."}

_START_DIRS = [
    Path(os.environ.get("APPDATA", str(Path.home() / "AppData" / "Roaming"))) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
]

# Browser executables for "open X in Chrome/Edge/Firefox/Brave".
_BROWSER_DIRS = [os.environ.get("PROGRAMFILES", r"C:\Program Files"),
    os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
    os.environ.get("LOCALAPPDATA", "") and os.path.join(os.environ["LOCALAPPDATA"], "Programs")]
_BROWSER_FILES = {"chrome": ("Google\\Chrome\\Application\\chrome.exe",),
    "edge": ("Microsoft\\Edge\\Application\\msedge.exe", "(x86)\\Microsoft\\Edge\\Application\\msedge.exe"),
    "firefox": ("Mozilla Firefox\\firefox.exe",),
    "brave": ("BraveSoftware\\Brave-Browser\\Application\\brave.exe",),
    "opera": ("Opera\\opera.exe", "Opera\\launcher.exe")}


def find_browser(name):
    """Resolve a spoken browser name to an executable path, or raise."""
    query = (name or "").strip().lower().replace("browser", "").strip()
    for key, files in _BROWSER_FILES.items():
        if key in query or query in key:
            for base in _BROWSER_DIRS:
                if not base:continue
                for f in files:
                    candidate = Path(base) / f
                    if candidate.is_file():
                        return str(candidate)
            raise ValueError(f"'{name}' does not seem to be installed.")
    raise ValueError(f"Unknown browser '{name}'. Try Chrome, Edge, Firefox, Brave, or Opera.")


def start_menu_apps():
    """Map lowercase shortcut name -> .lnk path for all Start Menu programs."""
    apps = {}
    for base in _START_DIRS:
        if base.is_dir():
            for path in base.rglob("*.lnk"):
                apps.setdefault(path.stem.lower(), path)
    return apps


_STORE_CACHE = {"loaded": False, "apps": {}}


def store_apps():
    """Lowercase app name -> AppID for installed apps (covers Windows Store apps like Calculator)."""
    if not _STORE_CACHE["loaded"]:
        script = """[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
@(Get-StartApps | ForEach-Object { @{name=$_.Name; id=$_.AppID} }) | ConvertTo-Json -Compress
"""
        encoded = base64.b64encode(script.encode("utf-16-le")).decode("ascii")
        try:
            p = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                               capture_output=True, timeout=60)
            data = json.loads(p.stdout.decode("utf-8-sig"))
            entries = data if isinstance(data, list) else [data]
            _STORE_CACHE["apps"] = {e["name"].lower(): e["id"] for e in entries if e.get("name") and e.get("id")}
        except Exception:
            _STORE_CACHE["apps"] = {}
        _STORE_CACHE["loaded"] = True
    return _STORE_CACHE["apps"]


def find_app(name):
    """Resolve a spoken app name to ("desktop", path, display) or ("store", appid, display)."""
    query = (name or "").strip().lower()
    if not query:
        raise ValueError("No application name was given.")
    apps = start_menu_apps()
    if query in apps:
        return ("desktop", apps[query], Path(apps[query]).stem)
    for key, path in sorted(apps.items()):
        if key.startswith(query):
            return ("desktop", path, Path(path).stem)
    store = store_apps()
    if query in store:
        return ("store", store[query], query)
    for key, appid in sorted(store.items()):
        if key.startswith(query):
            return ("store", appid, key)
    for key, path in sorted(apps.items()):
        if query in key:
            return ("desktop", path, Path(path).stem)
    for key, appid in sorted(store.items()):
        if query in key:
            return ("store", appid, key)
    raise ValueError(f"'{name}' is not in the Start Menu or installed-apps list.")


def press(vk):
    user32 = ctypes.windll.user32
    user32.keybd_event(vk, 0, 0, 0)
    user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP


def execute(cmd):
    """Validate and run one whitelisted command dict. Returns a spoken result string."""
    if not isinstance(cmd, dict):
        raise ValueError("Bad command.")
    action = str(cmd.get("action", "")).strip().lower()
    if action not in ALLOWED:
        raise ValueError(f"Action '{action}' is not allowed.")
    if action == "open_app":
        kind, target, display = find_app(str(cmd.get("app", "")))
        if kind == "desktop":
            os.startfile(str(target))
        else:
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + target])
        return f"Opening {display}."
    if action == "open_url":
        url = str(cmd.get("url", "")).strip()
        if not url.lower().startswith(("http://", "https://")):
            url = "https://" + url
        parsed = urlparse(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
            raise ValueError("Only http/https website addresses can be opened.")
        if "." not in parsed.netloc:
            # Bare word like "chrome": treat as an app name first (works even if misclassified).
            try:
                kind, target, display = find_app(parsed.netloc)
                if kind == "desktop":
                    os.startfile(str(target))
                else:
                    subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + target])
                return f"Opening {display}."
            except ValueError:
                raise ValueError("Only http/https website addresses can be opened.")
        if str(cmd.get("app", "")).strip():
            app_name = str(cmd.get("app", "")).strip()
            browser = find_browser(app_name)
            subprocess.Popen([browser, url], creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return f"Opening {url} in {app_name}."
        os.startfile(url)
        return "Opening " + url
    if action == "media":
        key = str(cmd.get("key", "")).strip().lower()
        if key not in _MEDIA_RESULT:
            raise ValueError("Unknown media key.")
        press(VK[key])
        return _MEDIA_RESULT[key]
    if action == "volume":
        direction = str(cmd.get("direction", "")).strip().lower()
        if direction not in _VOLUME_RESULT:
            raise ValueError("Unknown volume direction.")
        press(VK[direction])
        return _VOLUME_RESULT[direction]
    if action == "custom":
        return run_custom(str(cmd.get("name", "")))
    if action == "find_file":
        return find_file(str(cmd.get("name", "")),
                         str(cmd.get("open", "")).strip().lower() in ("1", "true", "yes"))
    # action == "lock"
    ctypes.windll.user32.LockWorkStation()
    return "Locking Windows."


def run_custom(name):
    """Run one user-defined command from pc-actions.json."""
    key, entry = _find_custom(name)
    if not entry.get("enabled", True):
        raise ValueError(f"Custom command '{key}' is disabled in pc-actions.json.")
    if len(custom_actions()) > 200:
        raise ValueError("Too many custom commands; keep pc-actions.json under 200 entries.")
    command = str(entry["command"]).strip()
    if not command:
        raise ValueError(f"Custom command '{key}' has an empty command.")
    subprocess.Popen(command, shell=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return str(entry.get("reply") or ("Done: " + key + "."))
