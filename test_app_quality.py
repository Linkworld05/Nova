# SPDX-License-Identifier: AGPL-3.0-or-later
"""Completion is backed by executed, current, requirement-mapped checks."""
import itertools
import json
from pathlib import Path
import shutil
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from app_quality import QualityGate,needs_quality,seed_requirements
import codex_agent
from codex_controls import Cancelled,RunControl

GOOD='def add(a,b):\n    return a+b\n'
TEST='import unittest\nimport app\nclass AppTests(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(app.add(2,3),5)\n'


class AppQualityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent,prefix='nova-quality-test-')
        self.root=Path(self.tmp.name);(self.root/'tests').mkdir();(self.root/'app.py').write_text(GOOD)
        (self.root/'tests/test_app.py').write_text(TEST)
    def tearDown(self):self.tmp.cleanup()
    def gate(self,question='Create a calculator app with addition.',review=None):
        gate=QualityGate(question,self.root,review_fn=review or (lambda *a:{'passed':True,'issues':[]}))
        gate.set_contract(self.contract(gate));return gate
    def contract(self,gate,test='test_add'):
        return {'entrypoint':'app.py','items':[{'id':r['id'],'acceptance':'Adding 2 and 3 returns 5','tests':[test],'manual':False} for r in gate.requirements]}
    def check(self,gate):return json.loads(gate.run_tests({'framework':'python','start':'tests'}))

    def test_clock_request_preserves_every_explicit_feature(self):
        q='Can you make a software? I want an alarm clock, with a cronometer, and custom alarms'
        self.assertTrue(needs_quality(q));seeds=seed_requirements(q)
        for phrase in ('alarm clock','cronometer','custom alarms'):self.assertTrue(any(phrase in r['text'] for r in seeds))
        self.assertFalse(needs_quality('Explain the app architecture.'))
        self.assertFalse(needs_quality('Explain how to create a calculator app.'))
        self.assertTrue(needs_quality('Add repeat days to the alarm clock.'))

    def test_omitted_requirement_cannot_be_removed_by_model(self):
        gate=QualityGate('Create an app with addition and subtraction.',self.root)
        args=self.contract(gate);args['items']=args['items'][:-1]
        with self.assertRaises(ValueError):gate.set_contract(args)

    def test_filename_is_not_split_into_fake_requirements(self):
        seeds=seed_requirements('Create an app. Running app.py must print 5.')
        self.assertTrue(any('app.py must print 5' in r['text'] for r in seeds))
        self.assertFalse(any(r['text']=='Running app' for r in seeds))

    def test_root_discovery_finds_tests_without_package_marker(self):
        gate=self.gate();result=json.loads(gate.run_tests({'framework':'python','start':'.','pattern':'test_*.py'}))
        self.assertEqual(len(result['tests']),1)

    def test_default_discovery_finds_root_level_tests(self):
        (self.root/'tests/test_app.py').unlink();(self.root/'test_root.py').write_text(TEST)
        gate=self.gate();result=json.loads(gate.run_tests({'framework':'python'}))
        self.assertEqual(len(result['tests']),1);self.assertEqual(result['tests'][0]['status'],'passed')

    def test_root_and_unpackaged_tests_directory_both_run(self):
        (self.root/'test_root.py').write_text(TEST.replace('test_add','test_root').replace('AppTests','RootTests'))
        (self.root/'tests/test_app.py').write_text(TEST.replace('2,3),5','2,3),99'))
        gate=self.gate();result=json.loads(gate.run_tests({'framework':'python'}))
        self.assertEqual(len(result['tests']),2);self.assertTrue(any(t['status']=='failed' for t in result['tests']));self.assertFalse(gate.ready())

    def test_explicit_tests_directory_falls_back_to_observed_root_tests(self):
        (self.root/'tests/test_app.py').unlink();(self.root/'tests').rmdir();(self.root/'test_root.py').write_text(TEST)
        for start in ('tests','./tests/','tests\\'):
            with self.subTest(start=start):
                gate=self.gate();result=json.loads(gate.run_tests({'framework':'python','start':start}))
                self.assertEqual(len(result['tests']),1)

    def test_gui_empty_folder_hint_is_not_an_acceptance_requirement(self):
        question='Create an alarm clock app with a stopwatch and custom alarms.'
        gate=QualityGate('Note: the working folder C:\\Projects\\Tests is EMPTY — there is no existing project here. Create everything from scratch in this folder.\n\n'+question,self.root)
        self.assertEqual(gate.question,question);self.assertEqual(gate.requirements,seed_requirements(question))

    def test_missing_entrypoint_explains_next_action_without_running(self):
        gate=self.gate();(self.root/'app.py').unlink()
        with patch('app_test_runner.execute_checks') as execute:
            with self.assertRaisesRegex(ValueError,'Write the implementation first'):gate.run_tests({'framework':'python'})
        execute.assert_not_called();self.assertIsNone(gate.checks)

    def test_long_sleep_is_reported_without_waiting_or_generating_more_tests(self):
        (self.root/'tests/test_app.py').write_text(TEST.replace('self.assertEqual','import time;time.sleep(65)\n        self.assertEqual'))
        gate=self.gate();gate.test_fn=lambda *a:self.fail('A timed-out suite needs correction, not extra tests');gate.apply_test_write=lambda *a:None
        result=json.loads(gate.run_tests({'framework':'python'}))
        self.assertTrue(any('long sleep (65s)' in issue and 'test_app.py:' in issue for issue in result['runner_issues']))
        self.assertIn('fake clock',result['next_action']);self.assertFalse(gate.ready())

    def test_test_author_retries_invalid_code_with_specific_feedback(self):
        (self.root/'tests/test_app.py').unlink();gate=self.gate();calls=[]
        def author(messages,*a):
            calls.append(json.dumps(messages))
            return {'content':'class broken(' if len(calls)==1 else TEST,'notes':'Unit checks'}
        gate.test_fn=author
        gate.apply_test_write=lambda args:(self.root/args['path']).write_text(args['content'])
        gate.run_tests({'framework':'python'});self.assertEqual(len(calls),2)
        self.assertIn('SyntaxError',calls[1]);self.assertEqual(gate.test_author_report['status'],'created')

    def test_failed_test_author_reports_error_in_tool_feedback(self):
        (self.root/'tests/test_app.py').unlink();gate=self.gate();gate.test_fn=lambda *a:{'content':'class broken(','notes':''}
        gate.apply_test_write=lambda *a:self.fail('Invalid code must not be written')
        result=json.loads(gate.run_tests({'framework':'python'}))
        self.assertTrue(any('SyntaxError' in issue for issue in result['runner_issues']))
        self.assertEqual(result['test_generation']['status'],'failed');self.assertFalse(gate.ready())

    def test_generated_missing_app_import_is_fixed_without_changing_assertions(self):
        (self.root/'tests/test_app.py').unlink();gate=self.gate()
        incomplete=TEST.replace('import app\n','').replace('app.add','add')
        gate.test_fn=lambda *a:{'content':incomplete,'notes':'Checks addition'}
        gate.apply_test_write=lambda args:(self.root/args['path']).write_text(args['content'])
        result=json.loads(gate.run_tests({'framework':'python'}))
        self.assertEqual(result['tests'][0]['status'],'passed');self.assertGreater(result['tests'][0]['app_lines'],0)
        content=next(self.root.glob('test_nova_acceptance_*.py')).read_text()
        self.assertIn('from app import add',content);self.assertIn('self.assertEqual(add(2,3),5)',content)

    def test_new_test_fixture_is_repaired_without_rewriting_app_or_user_tests(self):
        (self.root/'tests/test_app.py').unlink();gate=QualityGate('Fix the calculator app with addition.',self.root)
        broken=TEST.replace('app.add(2,3)','app.add(self.left,3)')
        repaired=broken.replace('    def test_add','    def setUp(self):self.left=2\n    def test_add')
        replies=iter([{'tool':'requirements',**self.contract(gate)},{'tool':'run_tests','framework':'python'},{'final':'Done.'}]);authors=iter([broken,repaired])
        final,_,_=codex_agent.run(gate.question,self.root,lambda *a,**k:{'message':{'content':json.dumps(next(replies))}},lambda _:False,max_steps=3,
             test_fn=lambda *a:{'content':next(authors),'notes':'Fix fixture; same assertion'},review_fn=lambda *a:{'passed':True,'issues':[]})
        self.assertIn('Acceptance verification:',final);self.assertEqual((self.root/'app.py').read_text(),GOOD)
        self.assertIn('self.left=2',next(self.root.glob('test_nova_acceptance_*.py')).read_text())

    def test_preflight_recovers_invented_mappings_from_real_test_source(self):
        calls=[]
        def review(messages,schema):
            if 'mappings' in schema['properties']:
                packet=json.loads(messages[1]['content']);self.assertIn('tests/test_app.py',packet['test_source'])
                return {'mappings':[{'requirement':r['id'],'tests':['test_add']} for r in packet['requirements']],'issues':[]}
            calls.append(messages);return {'passed':True,'issues':[]}
        gate=self.gate(review=review);contract=self.contract(gate,'invented_test');gate.set_contract(contract);self.check(gate)
        gate.prepare_completion();self.assertTrue(gate.ready());self.assertEqual(len(calls),1)

    def test_test_fixture_name_error_is_not_treated_as_an_app_repair(self):
        from app_quality import test_fixture_error
        self.assertTrue(test_fixture_error({'app_lines':0,'details':'File "test_generated.py", line 8\nNameError: name X is not defined'}))
        self.assertFalse(test_fixture_error({'app_lines':0,'details':'File "app.py", line 8\nNameError: name X is not defined'}))
        self.assertFalse(test_fixture_error({'app_lines':0,'details':'no traceback'}))

    def test_short_app_full_read_reaches_model_before_whole_file_replace(self):
        content=GOOD+'#'+('a'*4000)+'\n# UNIQUE_TAIL\n';(self.root/'app.py').write_text(content)
        gate=QualityGate('Fix the calculator app with addition.',self.root);calls=[]
        replies=iter([{'tool':'requirements',**self.contract(gate)},{'tool':'read','path':'app.py'},{'tool':'write','path':'app.py','content':GOOD},{'final':'Done.'}])
        def chat(messages,**kw):calls.append(json.dumps(messages));return {'message':{'content':json.dumps(next(replies))}}
        codex_agent.run(gate.question,self.root,chat,lambda _:False,max_steps=4,review_fn=lambda *a:{'passed':True,'issues':[]})
        delivered=json.loads(calls[2])[-1]['content']
        self.assertIn('UNIQUE_TAIL',delivered);self.assertNotIn('TRUNCATED',delivered);self.assertEqual((self.root/'app.py').read_text(),GOOD)

    def test_relative_runpy_entrypoint_records_actual_app_execution(self):
        (self.root/'app.py').write_text(GOOD+'\nif __name__=="__main__":print(add(2,3))\n')
        (self.root/'tests/test_app.py').write_text('import unittest,runpy,io,contextlib\nclass CLI(unittest.TestCase):\n def test_add(self):\n  output=io.StringIO()\n  with contextlib.redirect_stdout(output):runpy.run_path("app.py",run_name="__main__")\n  self.assertEqual(output.getvalue().strip(),"5")\n')
        gate=self.gate();result=json.loads(gate.run_tests({'framework':'python'}))
        self.assertEqual(result['tests'][0]['status'],'passed');self.assertGreater(result['tests'][0]['app_lines'],0)

    def test_missing_tests_are_authored_executed_repaired_and_undoable(self):
        from codex_controls import UndoJournal
        (self.root/'tests/test_app.py').unlink()
        placeholder='import unittest\nclass Placeholder(unittest.TestCase):\n def test_placeholder(self):self.assertTrue(True)\n'
        (self.root/'test_placeholder.py').write_text(placeholder)
        (self.root/'app.py').write_text(GOOD.replace('a+b','a-b'))
        gate=QualityGate('Fix the calculator app with addition.',self.root)
        replies=iter([{'tool':'requirements',**self.contract(gate)},{'tool':'run_tests','framework':'python'},{'tool':'review_app'},{'final':'Done.'}])
        journal=UndoJournal(self.root);calls=[]
        final,audit,_=codex_agent.run(gate.question,self.root,lambda *a,**k:{'message':{'content':json.dumps(next(replies))}},lambda _:False,max_steps=5,journal=journal,
             test_fn=lambda *a:calls.append(a) or {'content':TEST,'notes':'Real addition assertions'},
             repair_fn=lambda *a:{'reason':'Addition fix','changes':[{'path':'app.py','old':'a-b','new':'a+b'}]},review_fn=lambda *a:{'passed':True,'issues':[]})
        self.assertIn('Acceptance verification:',final);self.assertEqual(len(calls),1)
        self.assertTrue(any(v.startswith('QA tests:') for v in audit));self.assertEqual((self.root/'test_placeholder.py').read_text(),placeholder)
        self.assertEqual(journal.undo(),2);self.assertIn('a-b',(self.root/'app.py').read_text())
        self.assertFalse(list(self.root.glob('test_nova_acceptance_*.py')))

    def test_authored_placeholders_still_cannot_complete(self):
        (self.root/'tests/test_app.py').unlink();gate=QualityGate('Fix the calculator app with addition.',self.root)
        replies=iter([{'tool':'requirements',**self.contract(gate)},{'tool':'run_tests','framework':'python'},{'final':'Done.'}])
        calls=[]
        final,_,_=codex_agent.run(gate.question,self.root,lambda *a,**k:{'message':{'content':json.dumps(next(replies))}},lambda _:False,max_steps=3,
             test_fn=lambda *a:calls.append(a) or {'content':'import unittest\nclass Bad(unittest.TestCase):\n def test_add(self):self.assertTrue(True)\n','notes':'placeholder'},
             review_fn=lambda *a:{'passed':True,'issues':[]})
        self.assertIn('not fully verified',final);self.assertEqual(len(calls),1)

    def test_relative_pattern_is_normalized_without_shell_execution(self):
        gate=self.gate();result=json.loads(gate.run_tests({'framework':'python','start':'.','pattern':'tests/test_*.py'}))
        self.assertEqual(len(result['tests']),1)

    def test_empty_mappings_are_pending_not_a_pass(self):
        gate=self.gate();args=self.contract(gate)
        for item in args['items']:item['tests']=[]
        gate.set_contract(args);self.check(gate)
        self.assertTrue(any('no functional test is assigned' in x for x in gate.missing()));self.assertFalse(gate.ready())

    def test_mapping_links_only_observed_behavioral_results(self):
        gate=self.gate();self.check(gate)
        gate.map_tests({'requirement':'R1','tests':['test_add']})
        self.assertTrue(gate.checks['mapping_rebindings'])
        with self.assertRaises(ValueError):gate.map_tests({'requirement':'R1','tests':['made_up']})
        gate.review_app();self.assertTrue(gate.ready())

    def test_mapping_does_not_reuse_stale_results(self):
        gate=self.gate();self.check(gate);(self.root/'app.py').write_text(GOOD+'\n# new version')
        with self.assertRaises(ValueError):gate.map_tests({'requirement':'R1','tests':['test_add']})

    def test_preflight_can_link_missing_metadata_then_review(self):
        from app_quality import COVERAGE_SCHEMA
        def reviewer(messages,schema):
            return {'mappings':[{'requirement':'R1','tests':['test_add']}],'issues':[]} if schema==COVERAGE_SCHEMA else {'passed':True,'issues':[]}
        gate=self.gate(review=reviewer);args=self.contract(gate);args['items'][0]['tests']=[];gate.set_contract(args)
        self.check(gate);gate.prepare_completion();self.assertTrue(gate.ready(),gate.status())

    def test_no_execution_instruction_is_respected(self):
        gate=self.gate('Create a calculator app. Do not run code.')
        with patch('app_test_runner.execute_checks') as execute:
            gate.prepare_completion()
            with self.assertRaises(ValueError):self.check(gate)
        execute.assert_not_called();self.assertFalse(gate.ready())

    def test_old_test_commands_route_to_measured_runner_not_shell(self):
        gate=self.gate()
        self.assertEqual(gate.adapt_test_command('python app.py'),{'framework':'python'})
        self.assertEqual(gate.adapt_test_command('python -m pytest -v'),{'framework':'python'})
        self.assertIsNone(gate.adapt_test_command('python app.py & del a.txt'))

    def test_known_failed_implementation_cannot_be_reapplied(self):
        (self.root/'app.py').write_text(GOOD.replace('a+b','a-b'))
        gate=self.gate();self.check(gate)
        with self.assertRaisesRegex(ValueError,'already failed'):gate.check_candidate(self.root/'app.py',(GOOD.replace('a+b','a-b')+'\n# same bug').encode())

    def test_duplicate_startup_diagnostic_has_source_lines(self):
        from app_diagnostics import inspect_entry
        (self.root/'app.py').write_text(GOOD+'\nif __name__=="__main__":\n print(5)\nif __name__=="__main__":\n print(5)\n')
        self.assertTrue(any('2 __main__ startup blocks' in v and 'lines' in v for v in inspect_entry(self.root/'app.py')))

    def test_focused_repair_is_tested_and_undoable(self):
        from codex_controls import UndoJournal
        (self.root/'app.py').write_text(GOOD.replace('a+b','a-b'))
        gate=QualityGate('Fix the calculator app with addition.',self.root)
        replies=iter([{'tool':'requirements',**self.contract(gate)},{'tool':'run_tests','framework':'python'}, {'tool':'review_app'}, {'final':'Fixed.'}])
        journal=UndoJournal(self.root)
        repair=lambda *a:{'reason':'Correct subtraction','changes':[{'path':'app.py','old':'a-b','new':'a+b'}]}
        final,audit,_=codex_agent.run('Fix the calculator app with addition.',self.root,lambda *a,**k:{'message':{'content':json.dumps(next(replies))}},lambda _:False,max_steps=6,
                                    journal=journal,review_fn=lambda *a:{'passed':True,'issues':[]},repair_fn=repair)
        self.assertIn('Acceptance verification:',final);self.assertTrue(any(v.startswith('QA repair:') for v in audit))
        self.assertEqual(journal.undo(),1);self.assertIn('a-b',(self.root/'app.py').read_text())

    def test_tests_cannot_replace_the_code_being_verified(self):
        test='import unittest,app\nfrom pathlib import Path\nclass BadTest(unittest.TestCase):\n def test_add(self):\n  Path("app.py").write_text("def add(a,b):return 5")\n  self.assertEqual(app.add(2,3),5)\n'
        (self.root/'tests/test_app.py').write_text(test);gate=self.gate();result=self.check(gate)
        self.assertEqual(result['tests'][0]['status'],'error');self.assertFalse(gate.ready())

    def test_actual_behavior_evidence_and_review_are_both_required(self):
        gate=self.gate();self.assertFalse(gate.ready())
        result=self.check(gate);self.assertTrue(result['launch']['passed'])
        test=result['tests'][0];self.assertGreater(test['app_lines'],0);self.assertGreater(test['meaningful_assertions'],0)
        self.assertFalse(gate.ready());gate.review_app();self.assertTrue(gate.ready())

    def test_completion_report_cannot_claim_stale_or_missing_verification(self):
        gate=self.gate();self.assertIn('not fully verified',gate.completion_message())
        self.check(gate);gate.review_app()
        self.assertIn('Acceptance verification:',gate.completion_message())
        (self.root/'app.py').write_text(GOOD+'\n# revised')
        self.assertIn('not fully verified',gate.completion_message())

    def test_review_receives_helper_modules_as_well_as_entrypoint(self):
        calls=[]
        (self.root/'helper.py').write_text('def helper():return 99\n')
        gate=self.gate(review=lambda messages,*a:calls.append(messages) or {'passed':True,'issues':[]})
        self.check(gate);gate.review_app()
        self.assertIn('helper.py',json.loads(calls[0][1]['content'])['source_files'])

    def test_settings_panel_displays_persisted_requirement_and_real_test_results(self):
        from test_codex_stop_undo import isolated_app
        gate=self.gate();self.check(gate);gate.review_app();gate.persist('verified')
        # Isolated Nova root: no personal settings or production quality database.
        with isolated_app(gate.root) as app:
            self.assertTrue(any(app.tabs.tab(tab,'text')=='App checks' for tab in app.tabs.tabs()))
            rows=app.quality_tree.get_children();self.assertEqual(len(rows),1)
            app.quality_tree.selection_set(rows[0]);app.quality_tree.event_generate('<<TreeviewSelect>>');app.root.update()
            text=app.quality_details.get('1.0','end')
            self.assertIn('Status: verified',text);self.assertIn(gate.question,text)
            self.assertIn('Adding 2 and 3 returns 5',text);self.assertIn('PASSED:',text)

    def test_constant_assertion_and_zero_executed_app_code_do_not_pass(self):
        (self.root/'tests/test_app.py').write_text('import unittest\nclass FakeTests(unittest.TestCase):\n def test_add(self):\n  self.assertTrue(True)\n')
        gate=self.gate();self.check(gate)
        self.assertTrue(any('no observed nonconstant assertion' in x for x in gate.missing()))
        self.assertTrue(any('did not execute app code' in x for x in gate.missing()))

    def test_empty_suite_is_not_a_pass(self):
        (self.root/'tests/test_app.py').write_text('')
        gate=self.gate();self.check(gate)
        self.assertTrue(any('No tests' in x for x in gate.missing()));self.assertFalse(gate.ready())

    def test_skipped_test_does_not_cover_requirement(self):
        (self.root/'tests/test_app.py').write_text(TEST.replace('    def test_add','    @unittest.skip("later")\n    def test_add'))
        gate=self.gate();self.check(gate)
        self.assertTrue(any('did not pass' in x for x in gate.missing()))

    def test_subtest_failures_are_not_reported_as_passes(self):
        (self.root/'tests/test_app.py').write_text('import unittest,app\nclass AppTests(unittest.TestCase):\n def test_add(self):\n  with self.subTest("failure"):\n   self.assertEqual(app.add(2,3),99)\n')
        gate=self.gate();result=self.check(gate)
        self.assertEqual(result['tests'][0]['status'],'failed');self.assertFalse(gate.ready())

    def test_source_change_invalidates_test_and_review_evidence(self):
        gate=self.gate();self.check(gate);gate.review_app();self.assertTrue(gate.ready())
        (self.root/'app.py').write_text('def add(a,b):\n return 99\n')
        self.assertFalse(gate.ready());self.assertTrue(any('stale' in x for x in gate.missing()))

    def test_test_change_invalidates_evidence(self):
        gate=self.gate();self.check(gate);gate.review_app()
        (self.root/'tests/test_app.py').write_text(TEST+'\n# changed\n')
        self.assertFalse(gate.ready())

    def test_manual_requirement_stays_pending(self):
        gate=self.gate();args=self.contract(gate);args['items'][0]['manual']=True
        gate.set_contract(args);self.check(gate)
        self.assertTrue(any('manual verification' in x for x in gate.missing()))

    def test_failed_review_is_cached_until_sources_or_tests_change(self):
        calls=[]
        gate=self.gate(review=lambda *a:calls.append(a) or {'passed':False,'issues':['R3 lacks subtraction.']})
        self.check(gate);gate.review_app();gate.review_app()
        self.assertEqual(len(calls),1);self.assertFalse(gate.ready())

    def test_launch_failure_blocks_completion(self):
        (self.root/'app.py').write_text(GOOD+'\nif __name__=="__main__":\n raise RuntimeError("startup failed")\n')
        gate=self.gate();result=self.check(gate)
        self.assertEqual(result['tests'][0]['status'],'passed');self.assertFalse(result['launch']['passed']);self.assertFalse(gate.ready())

    def test_project_writes_do_not_touch_the_original_app_data(self):
        (self.root/'app.py').write_text('from pathlib import Path\ndef add(a,b):\n Path("created.txt").write_text("fixture")\n return a+b\n')
        gate=self.gate();self.check(gate)
        self.assertFalse((self.root/'created.txt').exists())

    def test_common_outside_write_attempt_is_blocked(self):
        target=self.root/'should-not-exist.txt'
        (self.root/'app.py').write_text('from pathlib import Path\ndef add(a,b):\n Path('+repr(str(target))+').write_text("bad")\n return a+b\n')
        gate=self.gate();result=self.check(gate)
        self.assertFalse(target.exists());self.assertEqual(result['tests'][0]['status'],'error');self.assertFalse(gate.ready())

    def test_stop_cancels_the_test_worker(self):
        (self.root/'tests/test_app.py').write_text(TEST.replace('self.assertEqual','import threading;threading.Event().wait(20)\n        self.assertEqual'))
        control=RunControl();gate=self.gate();gate.control=control
        timer=threading.Timer(.4,control.cancel);timer.start()
        try:
            with self.assertRaises(Cancelled):self.check(gate)
        finally:timer.cancel()
        self.assertIsNone(gate.checks)

    def test_tests_timeout_discards_previous_pass(self):
        gate=self.gate();self.check(gate)
        import subprocess
        with patch('app_test_runner.execute_checks',side_effect=subprocess.TimeoutExpired('fixture',45)):
            result=self.check(gate)
        self.assertTrue(any('45-second' in x for x in result['remaining']));self.assertFalse(gate.ready())

    def test_final_claim_without_checks_is_replaced_and_edit_requires_contract(self):
        replies=iter([{'tool':'write','path':'new.py','content':'print("fake")'},*([{'final':'The app is finished and fully tested.'}]*4)])
        final,_,messages=codex_agent.run('Create a calculator app.',self.root,lambda *a,**k:{'message':{'content':json.dumps(next(replies))}},lambda _:False,max_steps=4)
        self.assertFalse((self.root/'new.py').exists());self.assertIn('not fully verified',final)
        self.assertNotIn('fully tested',final);self.assertEqual(messages[-1]['content'],final)

    def test_continue_keeps_original_app_requirements_and_gate(self):
        history=[{'role':'user','content':'Create a clock app with a stopwatch and custom alarms.'},
                 {'role':'assistant','content':'Work is not fully verified.'}]
        calls=[]
        def chat(messages,**kw):
            calls.append(json.dumps(messages));return {'message':{'content':json.dumps({'final':'Finished.'})}}
        final,_,_=codex_agent.run('Continue',self.root,chat,lambda _:False,max_steps=1,history=history)
        self.assertIn('not fully verified',final)
        self.assertTrue(any('custom alarms' in value and 'APP ACCEPTANCE GATE' in value for value in calls))

    def test_gate_feeds_failure_back_and_allows_fix_test_review_finish(self):
        gate=QualityGate('Fix the calculator app with addition.',self.root)
        (self.root/'app.py').write_text(GOOD.replace('a+b','a-b'))
        replies=iter([{'tool':'requirements',**self.contract(gate)}, {'tool':'run_tests','framework':'python','start':'tests'},
                      {'final':'All done.'}, {'tool':'read','path':'app.py'}, {'tool':'edit','path':'app.py','old':'a-b','new':'a+b'},
                      {'tool':'run_tests','framework':'python','start':'tests'}, {'tool':'review_app'}, {'final':'The app is fixed.'}])
        calls=[]
        def chat(messages,**kw):calls.append(json.dumps(messages));return {'message':{'content':json.dumps(next(replies))}}
        final,_,_=codex_agent.run('Fix the calculator app with addition.',self.root,chat,lambda _:False,max_steps=12,review_fn=lambda *a:{'passed':True,'issues':[]})
        self.assertTrue(any('Finish blocked' in value and 'did not pass' in value for value in calls))
        self.assertIn('Acceptance verification:',final);self.assertNotIn('not fully verified',final)

    def test_node_runner_observes_named_tests_and_module_execution(self):
        if not shutil.which('node'):self.skipTest('Node unavailable')
        (self.root/'app.js').write_text('exports.add=(a,b)=>a+b;')
        (self.root/'tests/app.test.js').write_text("const {test}=require('node:test');const assert=require('node:assert/strict');const {add}=require('../app');test('addition works',()=>assert.equal(add(2,3),5));")
        gate=QualityGate('Create a calculator app with addition.',self.root,review_fn=lambda *a:{'passed':True,'issues':[]})
        args=self.contract(gate,'addition works');args['entrypoint']='app.js';gate.set_contract(args)
        result=json.loads(gate.run_tests({'framework':'node','start':'tests'}));self.assertTrue(result['tests'],result)
        gate.review_app();self.assertTrue(gate.ready(),gate.status())


if __name__=='__main__':unittest.main()
