# SPDX-License-Identifier: AGPL-3.0-or-later
"""Synthetic publication/privacy regressions; no original installation access."""
import contextlib
import io
import json
import shutil
import subprocess
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools.check_public_tree import inspect
from tools import download_models

ROOT=Path(__file__).resolve().parent


class PublicDistributionTests(unittest.TestCase):
    def test_runtime_files_and_model_weights_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'memory.sqlite3').write_text('fixture')
            (root/'models').mkdir();(root/'models/weights.gguf').write_text('fixture')
            _,issues=inspect(root,True)
            self.assertEqual({i['path'] for i in issues},{'memory.sqlite3','models/weights.gguf'})

    def test_tokens_and_personal_paths_are_rejected_without_returning_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);token='ghp_'+'x'*36
            (root/'unsafe.txt').write_text(token+'\n'+chr(67)+':'+chr(92)+'Users'+chr(92)+'PrivatePerson'+chr(92)+'Documents')
            _,issues=inspect(root,True)
            self.assertEqual({i['kind'] for i in issues},{'github-token','personal-windows-path'})
            self.assertNotIn(token,json.dumps(issues));self.assertNotIn('PrivatePerson',json.dumps(issues))

    def test_blank_example_credentials_are_allowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'settings.example.json').write_text(json.dumps({'password':'','api_key':''}))
            self.assertFalse(inspect(root,True)[1])

    def test_forced_tracked_private_file_is_rejected_and_ignore_rules_work(self):
        git=shutil.which('git')
        if not git:self.skipTest('Git unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.gitignore').write_text((ROOT/'.gitignore').read_text())
            subprocess.run([git,'init','-q',str(root)],check=True,capture_output=True)
            (root/'memory.sqlite3').write_text('synthetic')
            self.assertEqual(subprocess.run([git,'check-ignore','-q','memory.sqlite3'],cwd=root).returncode,0)
            self.assertEqual(subprocess.run([git,'check-ignore','-q','examples/nova-settings.example.json'],cwd=root).returncode,1)
            subprocess.run([git,'add','-f','memory.sqlite3'],cwd=root,check=True,capture_output=True)
            self.assertTrue(any(i['path']=='memory.sqlite3' for i in inspect(root)[1]))

    def test_literal_credential_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);key='pass'+'word';(root/'unsafe.py').write_text(key+' = '+repr('fixture-private-value'))
            self.assertTrue(any(i['kind']=='literal-credential' for i in inspect(root,True)[1]))

    def test_source_archive_refuses_private_data_and_preserves_existing_output(self):
        from tools.build_source_archive import build
        import zipfile
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'source';root.mkdir();output=Path(tmp)/'source.zip'
            (root/'README.md').write_text('Synthetic source fixture')
            (root/'memory.sqlite3').write_text('synthetic private fixture')
            with self.assertRaises(ValueError):build(root,output)
            self.assertFalse(output.exists());(root/'memory.sqlite3').unlink()
            result=build(root,output);self.assertEqual(result['files'],1)
            with zipfile.ZipFile(output) as archive:self.assertEqual(archive.namelist(),['Nova/README.md'])
            with self.assertRaises(FileExistsError):build(root,output)
            with self.assertRaises(ValueError):build(root,root/'bad.zip')

    def test_model_download_dry_run_has_no_network_or_subprocess(self):
        with patch.object(download_models.subprocess,'run') as run,patch.object(download_models.shutil,'which') as which,contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(download_models.main(['--profile','Fast','--vision','--embeddings','--whisper','--dry-run']),0)
        run.assert_not_called();which.assert_not_called()
        self.assertEqual(download_models.planned('Balanced'),['qwen3:14b'])

    def test_clean_environment_lock_has_only_version_pins(self):
        lines=[line for line in (ROOT/'requirements-windows-py313.lock.txt').read_text().splitlines() if line and not line.startswith('#')]
        self.assertTrue(lines)
        self.assertTrue(all('==' in line and ' @ ' not in line and '://' not in line and not line.startswith('-') for line in lines))

    def test_public_defaults_are_opt_in_and_ssh_rejects_unknown_keys(self):
        voice=(ROOT/'voice_ui.py').read_text(encoding='utf-8')
        self.assertIn('self.settings.get("pc_control",False)',voice)
        self.assertIn('self.settings.get("speak",False)',voice)
        agent=(ROOT/'codex_agent.py').read_text(encoding='utf-8')
        self.assertIn('client.load_system_host_keys()',agent);self.assertIn('paramiko.RejectPolicy()',agent)
        self.assertNotIn('paramiko.AutoAddPolicy()',agent)

    def test_workflow_is_read_only_and_avoids_privileged_pr_trigger(self):
        import yaml
        data=yaml.load((ROOT/'.github/workflows/tests.yml').read_text(encoding='utf-8'),Loader=yaml.BaseLoader)
        self.assertEqual(data['permissions'],{'contents':'read'})
        self.assertNotIn('pull_request_target',data['on'])
        steps=data['jobs']['windows-tests']['steps']
        uses=[step['uses'] for step in steps if 'uses' in step]
        self.assertTrue(uses);self.assertTrue(all(len(value.split('@')[1])==40 for value in uses))
        self.assertEqual(data['jobs']['windows-tests']['runs-on'],'windows-latest')

    def test_about_license_window_displays_legal_text(self):
        import tkinter as tk
        import public_info
        root=tk.Tk();root.withdraw()
        try:
            window=public_info.show_about(root);window.withdraw();root.update()
            def texts(parent):
                result=[]
                for widget in parent.winfo_children():
                    if isinstance(widget,tk.Text):result.append(widget.get('1.0','end'))
                    result.extend(texts(widget))
                return result
            text='\n'.join(texts(window))
            self.assertIn('GNU AFFERO GENERAL PUBLIC LICENSE',text);self.assertIn('WITHOUT ANY WARRANTY',text)
        finally:root.destroy()


if __name__=='__main__':unittest.main()
