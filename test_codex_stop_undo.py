# SPDX-License-Identifier: AGPL-3.0-or-later
"""Stop/Undo checks with temporary projects, a local HTTP fixture, and isolated Tk."""
import contextlib
import ctypes
import io
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import assistant
import codex_agent
import reminders
import voice_ui
from codex_controls import Cancelled, RunControl, UndoJournal, latest_undo, ollama_chat


def scripted(items):
    replies = iter(items)
    return lambda *a, **k: {"message": {"content": json.dumps(next(replies))}}


def temporary_project():
    return tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent, prefix="nova-controls-test-")


class FileControlTests(unittest.TestCase):
    def setUp(self):
        self.tmp = temporary_project()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def run_agent(self, items, control=None):
        journal = UndoJournal(self.root, "Fixture request")
        result = codex_agent.run("Change fixture files", self.root, scripted(items), lambda _: True,
                                 control=control, journal=journal)
        return result, journal

    def test_undo_repeated_edit_new_file_and_project_memory_after_reload(self):
        original = b"first\r\nsecond\r\n"
        (self.root / "old.txt").write_bytes(original)
        self.run_agent([
            {"tool": "edit", "path": "old.txt", "old": "first", "new": "changed"},
            {"tool": "edit", "path": "old.txt", "old": "changed", "new": "changed twice"},
            {"tool": "write", "path": "new/nested.txt", "content": "created"},
            {"tool": "remember", "text": "Fixture memory"}, {"final": "done"}])
        journal = latest_undo(self.root)
        self.assertEqual(len(journal.changes), 3)
        self.assertEqual(journal.undo(), 3)
        self.assertEqual((self.root / "old.txt").read_bytes(), original)
        self.assertFalse((self.root / "new").exists())
        self.assertFalse((self.root / "NOVA.md").exists())
        self.assertIsNone(latest_undo(self.root))

    def test_later_edit_refuses_entire_undo(self):
        for name in ("a.txt", "b.txt"):
            (self.root / name).write_text("old")
        _, journal = self.run_agent([
            {"tool": "edit", "path": "a.txt", "old": "old", "new": "new"},
            {"tool": "edit", "path": "b.txt", "old": "old", "new": "new"}, {"final": "done"}])
        (self.root / "b.txt").write_text("later user change")
        with self.assertRaisesRegex(ValueError, "later changes were kept"):
            journal.undo()
        self.assertEqual((self.root / "a.txt").read_text(), "new")
        self.assertEqual((self.root / "b.txt").read_text(), "later user change")

    def test_stop_between_steps_preserves_undo_and_prevents_next_write(self):
        control = RunControl()
        journal = UndoJournal(self.root)
        count = 0
        def chat(*a, **k):
            nonlocal count
            count += 1
            if count == 2:
                control.cancel()
            name = "first.txt" if count == 1 else "must-not-exist.txt"
            return {"message": {"content": json.dumps({"tool": "write", "path": name, "content": "x"})}}
        final, _, _ = codex_agent.run("work", self.root, chat, lambda _: True,
                                     control=control, journal=journal)
        self.assertIn("Stopped", final)
        self.assertFalse((self.root / "must-not-exist.txt").exists())
        self.assertTrue(journal.data["finished"])
        self.assertEqual(journal.undo(), 1)

    def test_verification_read_after_edit_returns_fresh_contents(self):
        (self.root / "a.txt").write_text("old")
        (_, audit, messages), _ = self.run_agent([
            {"tool": "read", "path": "a.txt", "start": 1, "lines": 1},
            {"tool": "edit", "path": "a.txt", "old": "old", "new": "new"},
            {"tool": "read", "path": "a.txt", "start": 1, "lines": 1}, {"final": "done"}])
        self.assertNotIn("cached", audit[2])
        self.assertTrue(any(m["content"].startswith("RESULT: <") and m["content"].endswith("new") for m in messages))

    def test_undo_rejects_target_outside_project(self):
        _, journal = self.run_agent([{"tool": "write", "path": "new.txt", "content": "x"}, {"final": "done"}])
        journal.data["entries"][0]["path"] = "../outside.txt"
        with self.assertRaisesRegex(ValueError, "inside the original project"):
            journal.undo()

    def test_command_effects_are_disclosed_and_not_reversed(self):
        def fake_run(*a, **k):
            return subprocess.CompletedProcess(a[0], 0, b"ok", b"")
        with patch.object(codex_agent.subprocess, "run", side_effect=fake_run):
            _, journal = self.run_agent([
                {"tool": "write", "path": "a.txt", "content": "x"},
                {"tool": "run", "cmd": "echo fixture"}, {"final": "done"}])
        self.assertEqual(journal.data["commands"], ["echo fixture"])
        self.assertEqual(journal.undo(), 1)


class CancellationTests(unittest.TestCase):
    def test_normal_chunked_stream_preserves_unicode(self):
        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.send_header('Transfer-Encoding','chunked');self.end_headers()
                payload = (json.dumps({'message':{'content':'Hola '}},ensure_ascii=False)+'\n'+
                           json.dumps({'message':{'content':'México'},'done':True},ensure_ascii=False)+'\n').encode('utf-8')
                for piece in (payload[:7],payload[7:-3],payload[-3:]):
                    self.wfile.write(f'{len(piece):X}\r\n'.encode()+piece+b'\r\n');self.wfile.flush()
                self.wfile.write(b'0\r\n\r\n');self.wfile.flush()
                self.close_connection=True
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
        try:
            result=ollama_chat([], 'fixture', 20, RunControl(),port=server.server_port)
            self.assertEqual(result['message']['content'],'Hola México')
        finally:
            server.shutdown();server.server_close();worker.join(2)

    def test_stop_terminates_owned_shell_child(self):
        with temporary_project() as tmp:
            root = Path(tmp)
            child = root / "sleeper.py"
            child.write_text("import os,time\nfrom pathlib import Path\nPath('pid.txt').write_text(str(os.getpid()))\ntime.sleep(30)\nPath('late.txt').write_text('bad')\n")
            control = RunControl()
            caught = []
            def run():
                try:
                    control.execute(subprocess.list2cmdline([sys.executable, str(child)]), root)
                except BaseException as e:
                    caught.append(e)
            worker = threading.Thread(target=run)
            worker.start()
            deadline = time.monotonic() + 8
            try:
                while not (root / "pid.txt").exists() and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue((root / "pid.txt").exists())
            finally:
                control.cancel()
                worker.join(12)
            self.assertFalse(worker.is_alive())
            self.assertTrue(caught and isinstance(caught[0], Cancelled), caught)
            self.assertFalse((root / "late.txt").exists())
            pid = int((root / "pid.txt").read_text())
            if os.name == "nt":
                kernel = ctypes.windll.kernel32
                kernel.OpenProcess.restype = ctypes.c_void_p
                handle = kernel.OpenProcess(0x1000, False, pid)
                if handle:
                    code = ctypes.c_ulong()
                    kernel.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
                    kernel.CloseHandle(ctypes.c_void_p(handle))
                    self.assertNotEqual(code.value, 259, "Owned child is still running")

    def test_model_stream_stops_while_waiting_for_a_token(self):
        ready, release = threading.Event(), threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.end_headers();self.wfile.flush()
                ready.set();release.wait(5)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server_thread = threading.Thread(target=server.serve_forever, daemon=True);server_thread.start()
        control, caught = RunControl(), []
        def generate():
            try:ollama_chat([], 'fixture', 10, control, port=server.server_port)
            except BaseException as e:caught.append(e)
        worker = threading.Thread(target=generate);worker.start()
        try:
            self.assertTrue(ready.wait(3))
            start = time.monotonic();control.cancel();worker.join(2)
            self.assertFalse(worker.is_alive())
            self.assertLess(time.monotonic()-start, 2)
            self.assertTrue(caught and isinstance(caught[0], Cancelled), caught)
        finally:
            control.cancel();release.set();worker.join(3)
            server.shutdown();server.server_close();server_thread.join(2)


@contextlib.contextmanager
def isolated_app(root):
    (root / 'voice-settings.json').write_text(json.dumps({'speak': False, 'wake': False, 'pc_control': False}))
    with contextlib.ExitStack() as stack:
        for module, name, value in [(assistant,'ROOT',root), (assistant,'SETTINGS_FILE',root/'nova-settings.json'),
                                    (reminders,'ROOT',root)]:
            stack.enter_context(patch.object(module, name, value))
        for name in ('voices','microphones','loopback_outputs'):
            stack.enter_context(patch.object(voice_ui,name,return_value=[]))
        stack.enter_context(patch.object(assistant,'refresh_profile'))
        app=assistant.App();app.root.withdraw()
        try:yield app
        finally:
            if app._codex_control is not None:app._codex_control.cancel()
            if app.busy:wait_app(app,lambda:not app.busy)
            app.closing=True;app.speaker.stop()
            for callback in app.root.tk.call('after','info'):app.root.after_cancel(callback)
            app.root.destroy()


def wait_app(app, condition, seconds=5):
    if condition():return
    deadline=time.monotonic()+seconds
    def check():
        if condition() or time.monotonic()>=deadline:app.root.quit()
        else:app.root.after(10,check)
    app.root.after(0,check)
    app.root.mainloop()
    if not condition():raise AssertionError(app.status.get())


class UIControlTests(unittest.TestCase):
    def test_undo_available_after_restart(self):
        with temporary_project() as tmp:
            root=Path(tmp);project=root/'project';project.mkdir();(project/'a.txt').write_text('old')
            with isolated_app(root) as app:
                app.codex_workdir=project;assistant.save_settings(codex_workdir=str(project))
                app.toggle_codex();app._auto_approve=True
                answers=scripted([{'tool':'edit','path':'a.txt','old':'old','new':'new'},{'final':'Done.'}])
                app._codex_chat=lambda *a,**k:answers()
                app.question.insert(0,'Update fixture');app.answer();wait_app(app,lambda:not app.busy)
            with isolated_app(root) as reopened:
                self.assertEqual(str(reopened.codex_undo_button['state']),'normal')
                with patch.object(assistant.messagebox,'askyesno',return_value=True):
                    reopened.codex_undo_button.invoke();wait_app(reopened,lambda:not reopened.busy)
                self.assertEqual((project/'a.txt').read_text(),'old')

    def test_stop_and_undo_buttons_and_later_edit_protection(self):
        with temporary_project() as tmp:
            root=Path(tmp);project=root/'project';project.mkdir();(project/'a.txt').write_text('old')
            with isolated_app(root) as app:
                app.codex_workdir=project;app.toggle_codex();app._auto_approve=True
                answers=scripted([{'tool':'edit','path':'a.txt','old':'old','new':'new'},
                                  {'tool':'write','path':'created.txt','content':'created'},{'final':'Done.'}])
                app._codex_chat=lambda messages,tokens=1000,control=None:answers()
                app.question.insert(0,'Update fixtures');app.answer()
                wait_app(app,lambda:not app.busy)
                self.assertEqual(str(app.codex_undo_button['state']),'normal')
                with patch.object(assistant.messagebox,'askyesno',return_value=True):
                    app.codex_undo_button.invoke();wait_app(app,lambda:not app.busy)
                self.assertEqual((project/'a.txt').read_text(),'old')
                self.assertFalse((project/'created.txt').exists())
                self.assertEqual(app.codex_contexts,{})
                def waiting_chat(messages,tokens=1000,control=None):
                    control.cancelled.wait(4);control.check()
                    raise AssertionError('Stop was not requested')
                app._codex_chat=waiting_chat
                app.question.insert(0,'Wait for Stop');app.answer()
                self.assertEqual(str(app.codex_stop_button['state']),'normal')
                original=app.codex_workdir;app.select_project(str(root))
                self.assertEqual(app.codex_workdir,original)
                app.codex_stop_button.invoke();wait_app(app,lambda:not app.busy)
                self.assertIn('Stopped.',app.chatview.get('1.0','end'))
                self.assertEqual(str(app.codex_stop_button['state']),'disabled')

    def test_stop_closes_pending_approval_without_executing_command(self):
        with temporary_project() as tmp:
            root=Path(tmp);project=root/'project';project.mkdir();(project/'a.txt').write_text('old')
            with isolated_app(root) as app:
                app.codex_workdir=project;app.toggle_codex();app._auto_approve=False
                app._codex_chat=lambda *a,**k:{'message':{'content':json.dumps({'tool':'run','cmd':'echo approved-only'})}}
                with patch.object(codex_agent,'_execute') as execute:
                    app.question.insert(0,'Request a command');app.answer()
                    wait_app(app,lambda:any(w.winfo_class()=='Toplevel' and w!=app.settings_win for w in app.root.winfo_children()))
                    app.codex_stop_button.invoke();wait_app(app,lambda:not app.busy)
                    execute.assert_not_called()
                self.assertIn('Stopped.',app.chatview.get('1.0','end'))


if __name__ == '__main__':
    unittest.main()
