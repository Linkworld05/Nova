# SPDX-License-Identifier: AGPL-3.0-or-later
"""Execute measured tests against a temporary project copy, not the working data."""
import json
import ast
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from codex_controls import RunControl,Cancelled
from agent_state import redact


def execute_checks(project,entrypoint,framework,start,pattern,manifest,control=None):
    control=control or RunControl();worker=Path(__file__).with_name('app_test_worker.py')
    control.check()
    if framework=='python':
        blocking=[]
        for name in manifest:
            if not Path(name).name.startswith('test') or not name.endswith('.py'):continue
            try:tree=ast.parse((project/name).read_text(encoding='utf-8-sig'))
            except (SyntaxError,OSError):continue
            time_names={a.asname or a.name for n in ast.walk(tree) if isinstance(n,ast.Import) for a in n.names if a.name=='time'}
            sleep_names={a.asname or a.name for n in ast.walk(tree) if isinstance(n,ast.ImportFrom) and n.module=='time' for a in n.names if a.name=='sleep'}
            for node in ast.walk(tree):
                if not isinstance(node,ast.Call):continue
                wall_sleep=(isinstance(node.func,ast.Attribute) and isinstance(node.func.value,ast.Name) and node.func.value.id in time_names and node.func.attr=='sleep') or (isinstance(node.func,ast.Name) and node.func.id in sleep_names)
                if wall_sleep and node.args and isinstance(node.args[0],ast.Constant) and isinstance(node.args[0].value,(int,float)) and node.args[0].value>10:
                    blocking.append(name+':'+str(node.lineno)+' contains a long sleep ('+str(node.args[0].value)+'s). Use a fake clock or controlled callbacks with real assertions instead of waiting.')
        if blocking:return {'framework':'python','tests':[],'issues':blocking,'launch':{'passed':False,'reason':'Tests need bounded, deterministic timing before execution.'},'output':'','workspace':'not executed'}
    owner=Path(__file__).resolve().parent
    with tempfile.TemporaryDirectory(dir=owner,prefix='nova-app-check-') as tmp:
        base=Path(tmp).resolve()
        if not base.is_relative_to(owner):raise ValueError('Test workspace escaped the application directory.')
        copy=base/'project';copy.mkdir()
        for relative in manifest:
            source=project/relative;target=copy/relative;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
        env=dict(os.environ)
        for name in list(env):
            if any(word in name.upper() for word in ('PASSWORD','TOKEN','SECRET','API_KEY','PRIVATE_KEY')):env.pop(name)
        for name in ('TEMP','TMP','TMPDIR'):env[name]=str(base)
        env['PYTHONDONTWRITEBYTECODE']='1';env['PYTHONUTF8']='1'
        protected=[name for name in manifest if Path(name).suffix.lower() in ('.py','.js','.mjs','.cjs','.ts','.tsx','.jsx','.html','.css')]
        config={'root':str(copy),'entrypoint':entrypoint,'start':start,'pattern':pattern,'base':str(base),'protected_sources':protected}
        cfg=base/'config.json';cfg.write_text(json.dumps(config),encoding='utf-8')
        issues=[];tests=[];launch={};output=''
        if framework=='python':
            report=base/'tests.json'
            process=control.execute_argv([sys.executable,'-X','utf8',str(worker),'tests',str(cfg),str(report)],copy,timeout=45,env=env)
            output=(process.stdout+process.stderr).decode('utf-8',errors='replace')[-6000:]
            if report.exists():
                data=json.loads(report.read_text(encoding='utf-8'));tests=data.get('tests',[]);issues.extend(data.get('issues',[]))
                if not data.get('successful'):issues.append('The unittest run reported failures or errors.')
            else:issues.append('Python test runner returned no measured results.')
            if process.returncode!=0 and not any(t['status'] in ('failed','error') for t in tests):issues.append('Python test process exited '+str(process.returncode)+'.')
        else:
            node=shutil.which('node')
            if not node:issues.append('Node.js is unavailable.')
            else:
                files=sorted((copy/start).glob(pattern))
                if not files:issues.append('No Node test files matched the pattern.')
                else:
                    coverage=base/'coverage';coverage.mkdir();env['NODE_V8_COVERAGE']=str(coverage)
                    if (project/'node_modules').is_dir():env['NODE_PATH']=str(project/'node_modules')
                    p=control.execute_argv([node,'--test','--test-reporter=junit',*[str(f) for f in files]],copy,timeout=45,env=env)
                    output=p.stdout.decode('utf-8',errors='replace')[-100000:]
                    import xml.etree.ElementTree as ET
                    try:
                        xml=ET.fromstring(output[output.find('<?xml'):])
                        covered=[]
                        for path in coverage.glob('*.json'):
                            value=json.loads(path.read_text())
                            for script in value.get('result',[]):
                                if script.get('url','').endswith(entrypoint.replace('\\','/')) and any(r.get('count',0)>0 for f in script.get('functions',[]) for r in f.get('ranges',[])):covered.append(entrypoint)
                        assertion_count=sum(len(__import__('re').findall(r'\b(?:assert\.|expect\()',f.read_text(encoding='utf-8'))) for f in files)
                        for case in xml.iter('testcase'):
                            tests.append({'id':case.attrib.get('name',''),'status':'failed' if case.find('failure') is not None or case.find('error') is not None else 'skipped' if case.find('skipped') is not None else 'passed',
                                          'app_lines':len(covered),'meaningful_assertions':assertion_count,'details':' '.join(case.itertext())[-2000:]})
                    except Exception:issues.append('Node runner produced no valid measured test report.')
                    if p.returncode and not any(t['status']=='failed' for t in tests):issues.append('Node tests exited '+str(p.returncode)+'.')
        if not tests:issues.append('No tests ran; an empty suite cannot pass acceptance.')
        # Launch uses a separate process; a startup does not stand in for functional coverage.
        if entrypoint.endswith('.py'):
            report=base/'launch.json'
            try:
                p=control.execute_argv([sys.executable,'-X','utf8',str(worker),'launch',str(cfg),str(report)],copy,timeout=5,env=env)
                launch=json.loads(report.read_text(encoding='utf-8')) if report.exists() else {'passed':False,'reason':'No startup report.'}
            except subprocess.TimeoutExpired:
                launch={'passed':False,'reason':'App did not exit after the controlled close, or startup did not reach an observed UI heartbeat.'}
        elif entrypoint.endswith(('.js','.cjs','.mjs')):
            launch={'passed':bool(tests) and any(t.get('app_lines') for t in tests),'kind':'module executed by node:test','limitation':'Browser/GUI startup is not established by module coverage.'}
        else:launch={'passed':False,'reason':'HTML/browser launch needs an actual browser test; no browser result was supplied.'}
        for name in protected:
            path=copy/name
            if not path.exists() or hashlib.sha256(path.read_bytes()).hexdigest()!=manifest[name]:issues.append('Tests changed source code inside the copy: '+name+'. No original-version pass is recorded.')
        return {'framework':framework,'tests':tests,'issues':issues,'launch':launch,'output':redact(output[-6000:]),
                'workspace':'temporary source copy','limits':'Not an OS security sandbox. Python worker blocks common outside writes, subprocesses and external sockets; Node tests require trusted project code.'}
