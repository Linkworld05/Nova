# SPDX-License-Identifier: AGPL-3.0-or-later
"""Measured Python unittest and Tk startup worker. Invoked only in copied projects."""
import ast
import inspect
import io
import json
import os
from pathlib import Path
import runpy
import sys
import textwrap
import threading
import traceback
import unittest


def main():
    mode,config_path,report_path=sys.argv[1:4]
    config=json.loads(Path(config_path).read_text(encoding='utf-8'));root=Path(config['root']).resolve();base=Path(config['base']).resolve()
    report=Path(report_path);sys.path.insert(0,str(root));sys.dont_write_bytecode=True
    protected={str((root/name).resolve()).casefold() for name in config.get('protected_sources',[])}
    os.chdir(root);roots=[];state={'tests':[],'issues':[]};active=[None]
    # Reduce accidental side effects. These Python hooks are not an OS sandbox.
    def writable(value):
        if isinstance(value,int):return
        try:path=Path(os.fsdecode(value)).resolve()
        except (TypeError,ValueError):return
        if not path.is_relative_to(base):raise PermissionError('Test write outside temporary workspace refused: '+path.name)
        if str(path).casefold() in protected:raise PermissionError('Tests cannot rewrite or remove the source being verified: '+path.name)
    def audit(event,args):
        if event=='open':
            filename,mode,flags=args
            if isinstance(mode,str) and any(c in mode for c in 'wax+') or isinstance(flags,int) and flags&(os.O_WRONLY|os.O_RDWR|os.O_CREAT|os.O_TRUNC|os.O_APPEND):writable(filename)
        elif event in ('os.remove','os.rmdir','os.mkdir'):writable(args[0])
        elif event in ('os.rename','os.link','os.symlink'):writable(args[0]);writable(args[1])
        elif event in ('subprocess.Popen','os.system','os.exec'):raise PermissionError('Subprocesses are disabled in the Python app-test worker.')
        elif event=='socket.connect':
            address=args[1]
            if isinstance(address,tuple) and str(address[0]) not in ('127.0.0.1','::1','localhost'):raise PermissionError('External network access is disabled during app tests.')
    sys.addaudithook(audit)
    try:
        import winsound
        winsound.Beep=lambda *a,**k:None;winsound.PlaySound=lambda *a,**k:None
    except ImportError:pass
    try:
        import tkinter as tk
        original=tk.Tk.__init__
        def init(window,*a,**kw):
            original(window,*a,**kw);window.withdraw();roots.append(window)
            if mode=='launch':
                def heartbeat():
                    report.write_text(json.dumps({'passed':True,'kind':'Tk window initialized and event loop heartbeat observed','title':window.title(),'limitation':'No audible sound or visual/manual styling approval is claimed.'}),encoding='utf-8')
                    window.destroy()
                window.after(200,heartbeat)
        tk.Tk.__init__=init
    except ImportError:pass
    if mode=='launch':
        try:
            runpy.run_path(str(root/config['entrypoint']),run_name='__main__')
            if not report.exists():
                expected_ui='tkinter' in (root/config['entrypoint']).read_text(encoding='utf-8-sig',errors='replace')
                report.write_text(json.dumps({'passed':not expected_ui,'kind':'CLI entrypoint exited without error' if not expected_ui else 'Tk app never reached its event-loop heartbeat.'}),encoding='utf-8')
        except BaseException as exc:
            if isinstance(exc,SystemExit) and exc.code in (0,None):
                if not report.exists():report.write_text(json.dumps({'passed':True,'kind':'CLI exited 0'}),encoding='utf-8')
            else:report.write_text(json.dumps({'passed':False,'reason':traceback.format_exc()[-3000:]}),encoding='utf-8')
        return
    targets={os.path.normcase(str(p.resolve())) for p in root.rglob('*.py') if not p.name.startswith('test') and 'tests' not in p.relative_to(root).parts}
    source_paths={}
    root_prefix=os.path.normcase(str(root))+os.sep
    cache={}
    def assertion_lines(path):
        if path in cache:return cache[path]
        lines=set()
        try:
            tree=ast.parse(Path(path).read_text(encoding='utf-8-sig'))
            for node in ast.walk(tree):
                args=None
                if isinstance(node,ast.Assert):args=[node.test]
                elif isinstance(node,ast.Call) and isinstance(node.func,ast.Attribute) and node.func.attr.startswith('assert'):args=node.args
                if args and any(any(isinstance(v,(ast.Name,ast.Attribute,ast.Subscript)) for v in ast.walk(arg)) for arg in args):lines.add(node.lineno)
        except (OSError,SyntaxError):pass
        cache[path]=lines;return lines
    def trace(frame,event,arg):
        current=active[0]
        if current is not None and event=='line':
            raw_filename=frame.f_code.co_filename
            if raw_filename not in source_paths:
                source_paths[raw_filename]=raw_filename if raw_filename.startswith('<') else os.path.normcase(str(Path(raw_filename).resolve()))
            filename=source_paths[raw_filename]
            if filename in targets:current['_app'].add((filename,frame.f_lineno))
            elif filename.startswith(root_prefix) and frame.f_lineno in assertion_lines(filename):current['_assert'].add((filename,frame.f_lineno))
        return trace
    class Result(unittest.TextTestResult):
        def startTest(self,test):
            super().startTest(test)
            row={'id':test.id(),'status':'passed','details':'','_app':set(),'_assert':set()};state['tests'].append(row);active[0]=row
        def addFailure(self,test,err):
            super().addFailure(test,err);active[0]['status']='failed';active[0]['details']=self._exc_info_to_string(err,test)[-4000:]
        def addError(self,test,err):
            super().addError(test,err);active[0]['status']='error';active[0]['details']=self._exc_info_to_string(err,test)[-4000:]
        def addSkip(self,test,reason):
            super().addSkip(test,reason);active[0]['status']='skipped';active[0]['details']=reason
        def addSubTest(self,test,subtest,err):
            super().addSubTest(test,subtest,err)
            if err is not None:
                active[0]['status']='failed';active[0]['details']=self._exc_info_to_string(err,test)[-4000:]
        def addExpectedFailure(self,test,err):
            super().addExpectedFailure(test,err);active[0]['status']='skipped';active[0]['details']='Expected failures do not verify acceptance.'
        def addUnexpectedSuccess(self,test):
            super().addUnexpectedSuccess(test);active[0]['status']='failed';active[0]['details']='Unexpected success in an expected-failure test.'
        def stopTest(self,test):
            row=active[0];row['app_lines']=len(row.pop('_app'));row['meaningful_assertions']=len(row.pop('_assert'));active[0]=None;super().stopTest(test)
    output=io.StringIO()
    try:
        start=root/config['start']
        top=root if start==root or (start/'__init__.py').exists() else start
        suite=unittest.TestLoader().discover(str(start),pattern=config['pattern'],top_level_dir=str(top))
        extra=root/'tests'
        if start==root and extra.is_dir() and not (extra/'__init__.py').exists():
            suite.addTests(unittest.TestLoader().discover(str(extra),pattern=config['pattern'],top_level_dir=str(extra)))
        sys.settrace(trace);threading.settrace(trace)
        result=unittest.TextTestRunner(stream=output,verbosity=2,resultclass=Result).run(suite)
        state['successful']=result.wasSuccessful()
    except BaseException:state['issues'].append(traceback.format_exc()[-4000:]);state['successful']=False
    finally:
        sys.settrace(None);threading.settrace(None)
        for window in roots:
            try:window.destroy()
            except Exception:pass
        for row in state['tests']:
            if '_app' in row:row['app_lines']=len(row.pop('_app'));row['meaningful_assertions']=len(row.pop('_assert'))
        state['output']=output.getvalue()[-8000:];report.write_text(json.dumps(state),encoding='utf-8')
    print(state['output'])


if __name__=='__main__':main()
