# SPDX-License-Identifier: AGPL-3.0-or-later
"""Cancellation and conflict-checked, persistent undo for Nova's file tools."""
import hashlib
import http.client
import io
import json
import os
import socket
import subprocess
import threading
import time
import uuid
from pathlib import Path


class Cancelled(Exception):
    pass


class RunControl:
    def __init__(self):
        self.cancelled = threading.Event()
        self._lock = threading.Lock()
        self._socket = None
        self.messages = []
        self.audit = []

    def check(self):
        if self.cancelled.is_set():
            raise Cancelled("Stopped by the user.")

    def cancel(self):
        self.cancelled.set()
        with self._lock:
            sock = self._socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def attach_socket(self, sock):
        with self._lock:
            self._socket = sock
        if self.cancelled.is_set():
            self.cancel()
            self.check()

    def detach_socket(self):
        with self._lock:
            sock = self._socket
            self._socket = None
        if sock is not None:
            sock.close()

    @staticmethod
    def _terminate(process):
        if process.poll() is not None:
            return
        if os.name == "nt":
            # Only the shell launched by this run and its descendants.
            subprocess.run(["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                           capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            import signal
            os.killpg(process.pid, signal.SIGKILL)
        if process.poll() is None:
            process.kill()

    def execute(self, cmd, cwd, timeout=120):
        return self._execute_process(cmd,cwd,timeout,shell=True)

    def execute_argv(self,argv,cwd,timeout=120,env=None):
        """No shell parsing for trusted test-worker invocations."""
        return self._execute_process(argv,cwd,timeout,shell=False,env=env)

    def _execute_process(self,cmd,cwd,timeout,shell,env=None):
        self.check()
        process = subprocess.Popen(cmd, shell=shell, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=str(cwd),
                                   env=env,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                   start_new_session=os.name != "nt")
        started = time.monotonic()
        try:
            while True:
                self.check()
                if time.monotonic() - started > timeout:
                    raise subprocess.TimeoutExpired(cmd, timeout)
                try:
                    stdout, stderr = process.communicate(timeout=.1)
                    self.check()
                    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    continue
        finally:
            if process.poll() is None:
                self._terminate(process)
            try:
                process.communicate(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            for pipe in (process.stdout, process.stderr):
                if pipe is not None:
                    pipe.close()


class _InterruptibleReader(io.RawIOBase):
    """Keep socket timeouts out of BufferedReader's file-object state."""
    def __init__(self, sock, control):
        self.sock, self.control = sock.dup(), control
        self.sock.settimeout(.1)

    def readable(self):
        return True

    def readinto(self, buffer):
        started = time.monotonic()
        while True:
            self.control.check()
            try:
                count = self.sock.recv_into(buffer)
                self.control.check()
                return count
            except socket.timeout:
                if time.monotonic() - started > 240:
                    raise TimeoutError("The local model did not respond for four minutes.")
            except OSError:
                self.control.check()
                raise

    def close(self):
        self.sock.close()
        super().close()


class _InterruptibleSocket:
    def __init__(self, sock, control):
        self.sock, self.control = sock, control

    def makefile(self, mode):
        return io.BufferedReader(_InterruptibleReader(self.sock, self.control))


def ollama_chat(messages, model, tokens, control, host="127.0.0.1", port=11434, options=None, think=False,response_format=None):
    """Stream the local reply over a socket that Stop can interrupt immediately."""
    control.check()
    connection = http.client.HTTPConnection(host, port, timeout=5)
    connection.response_class = lambda sock, **kwargs: http.client.HTTPResponse(
        _InterruptibleSocket(sock, control), **kwargs)
    response = None
    try:
        connection.connect()
        connection.sock.settimeout(240)
        # HTTP/1.0 or Connection: close can detach/close HTTPConnection.sock
        # while HTTPResponse still reads its file handle. Keep an independent
        # handle so Stop can shut down that same connection in either case.
        control.attach_socket(connection.sock.dup())
        control.check()
        from agent_protocol import SCHEMA
        body = json.dumps({"model": model, "messages": messages, "stream": True, "format": response_format or SCHEMA,
                           "think": think, "keep_alive": "5m",
                           "options": options or {"num_ctx": 8192, "num_predict": tokens,
                                                  "temperature": .2}})
        connection.request("POST", "/api/chat", body.encode("utf-8"),
                           {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"Local model returned HTTP {response.status}.")
        parts = []
        while True:
            control.check()
            line = response.readline()
            control.check()
            if not line:
                break
            item = json.loads(line)
            if item.get("error"):
                raise RuntimeError(item["error"])
            parts.append(item.get("message", {}).get("content", ""))
            if item.get("done"):
                return dict(item, message={"role": "assistant", "content": "".join(parts)})
        raise RuntimeError("The local model disconnected before completing its reply.")
    except (OSError, http.client.HTTPException):
        control.check()
        raise
    finally:
        control.detach_socket()
        if response is not None:
            response.close()
        connection.close()


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _file_hash(path):
    if not path.exists():
        return None
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"Cannot restore a non-regular file: {path.name}")
    return _digest(path.read_bytes())


def _inside(path, root):
    return path.is_relative_to(root)


class UndoJournal:
    """Only explicit file-tool mutations are reversible; commands are disclosed."""
    def __init__(self, workdir, question=""):
        self.root = Path(workdir).resolve()
        self.folder = self.root / ".nova-backups" / "undo" / (time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex)
        self.path = self.folder / "journal.json"
        self.data = {"version": 1, "root": str(self.root), "question": question[:200],
                     "created_ns": time.time_ns(), "entries": [], "commands": [],
                     "finished": False, "undone": False}

    def _save(self):
        if not _inside(self.folder.resolve(), self.root):
            raise ValueError("Nova's backup folder points outside the project.")
        self.folder.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(self.data, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def _target(self, relative):
        raw = self.root / relative
        target = raw.resolve()
        if raw.is_symlink() or not _inside(target, self.root) or target == self.root:
            raise ValueError("Undo target must be a file inside the original project.")
        if ".nova-backups" in target.relative_to(self.root).parts:
            raise ValueError("Nova's backups cannot be changed by its file tools.")
        return target

    def before(self, path):
        target = self._target(str(Path(path).relative_to(self.root)))
        relative = str(target.relative_to(self.root))
        entry = next((e for e in self.data["entries"] if e["path"] == relative), None)
        if entry is not None:
            if _file_hash(target) != entry["after"]:
                raise ValueError(f"{relative} changed outside this request; read it again before continuing.")
            return
        original = target.read_bytes() if target.exists() else None
        parents = []
        parent = target.parent
        while parent != self.root and not parent.exists():
            parents.append(str(parent.relative_to(self.root)))
            parent = parent.parent
        entry = {"path": relative, "before": _digest(original) if original is not None else None,
                 "after": _digest(original) if original is not None else None,
                 "backup": f"{len(self.data['entries'])}.bin" if original is not None else None,
                 "mode": target.stat().st_mode if original is not None else None,
                 "parents": parents, "restored": False}
        self._save()
        if original is not None:
            (self.folder / entry["backup"]).write_bytes(original)
        self.data["entries"].append(entry)
        self._save()

    def after(self, path):
        relative = str(Path(path).relative_to(self.root))
        entry = next(e for e in self.data["entries"] if e["path"] == relative)
        entry["after"] = _file_hash(self._target(relative))
        self._save()

    def command(self, command):
        self.data["commands"].append(str(command)[:500])
        self._save()

    def finish(self):
        self.data["finished"] = True
        if self.data["entries"] or self.data["commands"]:
            self._save()

    @property
    def changes(self):
        return [e for e in self.data["entries"] if e["before"] != e["after"]]

    def undo(self):
        if not self.data["finished"]:
            raise ValueError("Stop the coding request and wait for it to finish before undoing.")
        if self.data["undone"]:
            raise ValueError("This request has already been undone.")
        changes = self.changes
        # Validate every file and backup first. Preserve all later user edits.
        for entry in changes:
            current = _file_hash(self._target(entry["path"]))
            expected = entry["before"] if entry["restored"] else entry["after"]
            if current != expected:
                raise ValueError(f"Undo refused: {entry['path']} changed after Nova's edit. Your later changes were kept.")
            if entry["backup"]:
                backup = self.folder / entry["backup"]
                if backup.parent.resolve() != self.folder.resolve() or _file_hash(backup) != entry["before"]:
                    raise ValueError("Undo backup is missing or damaged; no files were restored.")
        restored = 0
        for entry in reversed(changes):
            if entry["restored"]:
                continue
            target = self._target(entry["path"])
            if _file_hash(target) != entry["after"]:
                raise ValueError(f"Undo stopped: {entry['path']} changed during restoration.")
            if entry["backup"]:
                target.write_bytes((self.folder / entry["backup"]).read_bytes())
                os.chmod(target, entry["mode"])
            else:
                target.unlink()
            entry["restored"] = True
            restored += 1
            self._save()
        for parent in sorted({p for e in changes for p in e["parents"]}, key=lambda p: len(Path(p).parts), reverse=True):
            target = (self.root / parent).resolve()
            if target != self.root and _inside(target, self.root):
                try:
                    target.rmdir()  # Only empty directories created by this request.
                except OSError:
                    pass
        self.data["undone"] = True
        self._save()
        return restored


def latest_undo(workdir):
    root = Path(workdir).resolve()
    base = root / ".nova-backups" / "undo"
    if not _inside(base.resolve(), root) or not base.is_dir():
        return None
    candidates = []
    for path in base.glob("*/journal.json"):
        try:
            if not _inside(path.resolve(), root):
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("version") != 1 or Path(data["root"]).resolve() != root:
                continue
            if not data.get("finished") or data.get("undone"):
                continue
            journal = UndoJournal.__new__(UndoJournal)
            journal.root, journal.folder, journal.path, journal.data = root, path.parent, path, data
            if journal.changes:
                candidates.append(journal)
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return max(candidates, key=lambda j: j.data["created_ns"], default=None)
