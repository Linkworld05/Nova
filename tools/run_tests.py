# SPDX-License-Identifier: AGPL-3.0-or-later
"""Run only isolated offline checks, without loading personal Nova data."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT=Path(__file__).resolve().parents[1]
MODULES=['verify_project_fixes','verify_picture_attachment','verify_clipboard_pictures',
         'verify_grouped_chats','verify_send_behavior','verify_manual_library',
         'verify_website_manuals','verify_spanish_voice','verify_wake_cleanup',
         'test_codex_stop_undo','test_agent_upgrade','test_verified_reasoning',
         'test_app_quality','test_public_distribution']


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--core-only',action='store_true')
    args=parser.parse_args()
    if sys.platform!='win32':parser.error('The desktop suite requires Windows and Tk. No cross-platform pass is claimed.')
    folder=ROOT/'.test-tmp';folder.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=folder,prefix='offline-') as tmp:
        assert Path(tmp).resolve().is_relative_to(folder.resolve())
        env=dict(os.environ,PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',TEMP=tmp,TMP=tmp)
        # Import source from this checkout, but point runtime storage elsewhere.
        code='import sys,tempfile,unittest;from pathlib import Path;import assistant,reminders;root=Path(sys.argv[1]);assistant.ROOT=root;assistant.SETTINGS_FILE=root/"nova-settings.json";reminders.ROOT=root;tempfile.tempdir=str(root);suite=unittest.defaultTestLoader.loadTestsFromNames(sys.argv[2:]);result=unittest.TextTestRunner(verbosity=1).run(suite);raise SystemExit(not result.wasSuccessful())'
        result=subprocess.run([sys.executable,'-X','utf8','-c',code,tmp,*MODULES],cwd=ROOT,env=env)
        if result.returncode:return result.returncode
        if not args.core_only:
            result=subprocess.run([sys.executable,'tools/run_regression_scripts.py'],cwd=ROOT,env=env)
            if result.returncode:return result.returncode
            # JSON protocol script is a separate executable check, not unittest.
            code='import tempfile,runpy,sys;tempfile.tempdir=sys.argv[1];runpy.run_path("test_codex_json_salvage.py",run_name="isolated_check")'
            result=subprocess.run([sys.executable,'-X','utf8','-c',code,tmp],cwd=ROOT,env=env)
            if result.returncode:return result.returncode
    print('Offline checks completed. No live-model, audible, real-device or remote-server result is claimed.')
    return 0


if __name__=='__main__':raise SystemExit(main())
