# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run legacy checks in isolated subprocesses; never use Nova's personal DBs."""
import subprocess
import sys
import tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
SCRIPTS=['test_codex_bigfile.py','test_codex_declined.py','test_codex_memory.py','test_codex_progress.py','test_codex_readonly.py',
         'test_manual_context.py','test_memory_commands.py','test_regressions_nov3.py','test_regressions_round2.py','test_regressions_round3.py']
for script in SCRIPTS:
    with tempfile.TemporaryDirectory(dir=ROOT,prefix='nova-legacy-check-') as tmp:
        code="import runpy,tempfile,sys; from pathlib import Path; import assistant,reminders; root=Path(sys.argv[1]); assistant.ROOT=root; assistant.SETTINGS_FILE=root/'nova-settings.json'; reminders.ROOT=root; tempfile.tempdir=str(root); runpy.run_path(sys.argv[2],run_name='isolated_legacy_check')"
        result=subprocess.run([sys.executable,'-X','utf8','-c',code,tmp,str(ROOT/script)],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=120)
        print(script+(': OK' if result.returncode==0 else ': FAILED'),flush=True)
        if result.returncode:
            print(result.stdout+result.stderr);raise SystemExit(result.returncode)
