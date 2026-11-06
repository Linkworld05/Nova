# SPDX-License-Identifier: AGPL-3.0-or-later
"""Single-instance desktop launcher with a local error log."""
import sys, traceback, msvcrt, ctypes
from pathlib import Path
ROOT=Path(__file__).resolve().parent
lock=(ROOT/".assistant.lock").open("a+b")
lock.seek(0);lock.write(b"1");lock.flush();lock.seek(0)
try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
except OSError:
    ctypes.windll.user32.MessageBoxW(0,"Local Conversation Memory is already open. Restore its window from the taskbar.","Local Conversation Memory",64)
    raise SystemExit(0)
sys.stderr=(ROOT/"app.log").open("a",encoding="utf-8",buffering=1)
try:
    import assistant
    app=assistant.App()
    def callback_error(kind,value,tb):
        traceback.print_exception(kind,value,tb,file=sys.stderr)
        app.handsfree=False;app.speaker.stop()
        if app.recorder:
            try:app.stop_recording()
            except Exception:traceback.print_exc(file=sys.stderr)
        app.status.set("An error was logged to app.log. Saved audio remains available.")
    app.root.report_callback_exception=callback_error
    app.run()
except Exception:
    traceback.print_exc(file=sys.stderr)
    ctypes.windll.user32.MessageBoxW(0,f"The assistant could not start. Details are in {ROOT / 'app.log'}.","Assistant startup error",16)
    raise

