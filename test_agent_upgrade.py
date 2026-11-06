# SPDX-License-Identifier: AGPL-3.0-or-later
"""Regression cases for evidence, state isolation, retrieval and tool boundaries."""
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import agent_protocol
from agent_state import AgentState, compact, redact
import assistant
import codex_agent
from codex_controls import Cancelled, RunControl
from grounding import checked_answer, issues
from manual_library import ManualLibrary
import model_runtime
from semantic_index import SemanticIndex


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent,prefix='nova-upgrade-test-')
        self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()

    def test_state_survives_restart_and_isolates_chat_and_project(self):
        AgentState(self.root).save('one',self.root/'a',[{'role':'user','content':'keep x'}],'finished')
        state=AgentState(self.root)
        self.assertEqual(state.load('one',self.root/'a')[0]['content'],'keep x')
        self.assertIsNone(state.load('two',self.root/'a'))
        self.assertIsNone(state.load('one',self.root/'b'))
        state.clear_project(self.root/'a')
        self.assertIsNone(state.load('one',self.root/'a'))

    def test_state_redacts_named_credentials_and_bearer_tokens(self):
        content='vps_password = "abc123"\nOPENAI_API_KEY=sk-fixture\nDB_PASS=fixture\nAuthorization: Bearer token.fixture\nmysql://alice:pass@localhost/db'
        AgentState(self.root).save('one',self.root,[{'role':'user','content':content}])
        value=AgentState(self.root).load('one',self.root)[0]['content']
        for secret in ('abc123','sk-fixture','DB_PASS=fixture','token.fixture',':pass@'):
            self.assertNotIn(secret,value)

    def test_compaction_preserves_goal_plan_and_newest_result(self):
        messages=[{'role':'system','content':'system'}, {'role':'user','content':'Change file, preserve dates.'}]
        messages += [{'role':'user','content':'RESULT: '+'x'*1000} for _ in range(20)]
        messages += [{'role':'user','content':'RESULT: PLAN (self-reported)\n[active] verify'}, {'role':'user','content':'RESULT: exact error 42'}]
        reduced,changed=compact(messages,3000)
        joined=json.dumps(reduced)
        self.assertTrue(changed)
        for text in ('preserve dates','[active] verify','exact error 42'):self.assertIn(text,joined)
        self.assertLessEqual(sum(len(m['content']) for m in reduced),3000)

    def test_invalid_arguments_never_execute_and_can_be_repaired(self):
        replies=iter([{'tool':'write','path':'bad.txt','content':42}, {'tool':'write','path':'good.txt','content':'ok'}, {'final':'done'}])
        _,audit,_=codex_agent.run('fixture',self.root,lambda *a,**k:{'message':{'content':json.dumps(next(replies))}},lambda _:False)
        self.assertFalse((self.root/'bad.txt').exists())
        self.assertEqual((self.root/'good.txt').read_text(),'ok')
        self.assertEqual(len(audit),1)

    def test_mixed_final_and_action_rejected(self):
        with self.assertRaises(ValueError):agent_protocol.validate({'final':'done','tool':'write','path':'a','content':'bad'})
        with self.assertRaises(ValueError):agent_protocol.validate({'tool':'read','path':'a','lines':True})
        with self.assertRaises(ValueError):agent_protocol.validate({'tool':'run','cmd':'echo hi','typo':'x'})

    def test_plan_requires_valid_status_and_checkpoint_keeps_it(self):
        with self.assertRaises(ValueError):agent_protocol.validate({'tool':'plan','steps':[{'text':'x','status':'maybe'}]})
        replies=iter([{'tool':'plan','steps':[{'text':'Verify','status':'active'}]}, {'final':'waiting'}])
        snapshots=[]
        codex_agent.run('task',self.root,lambda *a,**k:{'message':{'content':json.dumps(next(replies))}},lambda _:False,
                        checkpoint=lambda m,s:snapshots.append((json.dumps(m),s)))
        self.assertTrue(any('RESULT: PLAN' in value for value,_ in snapshots))

    def test_repeated_tool_loop_stops_with_no_completion_claim(self):
        reply={'message':{'content':json.dumps({'tool':'list','path':'.'})}}
        final,audit,_=codex_agent.run('task',self.root,lambda *a,**k:reply,lambda _:False,max_steps=20)
        self.assertIn('not confirmed complete',final)
        # Loop guard allows up to 5 identical calls; the 6th is refused, so 5 ran.
        self.assertEqual(len(audit),5)

    def test_shell_substitution_is_not_auto_approved(self):
        for cmd in ('powershell -Command "git status $(Write-Output fixture)"','git log `whoami`','git diff --ext-diff'):
            approved=[]
            with patch.object(codex_agent,'_execute') as execute:
                result=codex_agent._tool_run({'cmd':cmd},self.root,lambda s:approved.append(s) or False)
            self.assertTrue(approved)
            self.assertIn('USER DECLINED',result)
            execute.assert_not_called()

    def test_numeric_hallucination_is_withheld_even_with_valid_citation(self):
        sources=[{'label':'[M1 p.2]','text':'Maximum pressure: 420 kPa.'}]
        value=checked_answer('Use 600 kPa [M1 p.2]',sources,'pressure?',lambda _:'Use 600 kPa [M1 p.2]')
        self.assertIn('cannot confirm',value)
        self.assertNotIn('600',value)
        self.assertFalse(issues('Maximum 420 kPa [M1 p.2]',sources))

    def test_single_repair_can_recover_valid_answer(self):
        sources=[{'label':'[M1 p.2]','text':'Maximum pressure: 420 kPa.'}]
        calls=[]
        value=checked_answer('Use 600 kPa [M9 p.8]',sources,'pressure?',lambda reason:calls.append(reason) or 'Maximum 420 kPa [M1 p.2]')
        self.assertEqual(len(calls),1)
        self.assertIn('420',value)

    def seed(self):
        library=ManualLibrary(self.root)
        with library.connect() as db:
            db.execute("INSERT INTO manuals VALUES(1,'Pump guide','pump.txt','one',1,'one',2,'now','fixture')")
            db.execute("INSERT INTO chunks VALUES(1,1,1,'Turn the switch off before servicing.')")
            db.execute("INSERT INTO chunks VALUES(2,1,2,'Lubricate the bearings monthly.')")
        return library

    def test_hybrid_finds_paraphrase_that_lexical_misses(self):
        library=self.seed()
        question='¿Cómo desenergizo previamente a una reparación?'
        self.assertFalse(library.retrieve(question,'1'))
        model_runtime.configure(self.root,semantic_search=True)
        with patch.object(SemanticIndex,'model_identity',return_value='fixture'),patch('semantic_index.embed',return_value=[[1.,0.],[0.,1.]]):
            result=library.semantic.build(library)
            self.assertEqual(result['updated'],2)
        with patch.object(SemanticIndex,'model_identity',return_value='fixture'),patch('semantic_index.embed',return_value=[[1.,0.]]):
            self.assertEqual(library.retrieve(question,'1')[0]['page'],1)

    def test_index_is_incremental_and_model_change_is_not_mixed(self):
        library=self.seed()
        with patch.object(SemanticIndex,'model_identity',return_value='fixture'),patch('semantic_index.embed',return_value=[[1.,0.],[0.,1.]]) as embed:
            library.semantic.build(library)
            self.assertEqual(library.semantic.build(library)['updated'],0)
            self.assertEqual(embed.call_count,1)
        model_runtime.configure(self.root,semantic_search=True)
        with library.connect() as db:rows=db.execute('SELECT c.*,m.title FROM chunks c JOIN manuals m ON m.id=c.manual_id').fetchall()
        with patch.object(SemanticIndex,'model_identity',return_value='changed'),patch('semantic_index.embed') as embed:
            self.assertEqual(library.semantic.scores('repair',rows),{})
            embed.assert_not_called()

    def test_embedding_outage_falls_back_to_lexical(self):
        library=self.seed()
        model_runtime.configure(self.root,semantic_search=True)
        with patch.object(SemanticIndex,'model_identity',return_value='fixture'),patch('semantic_index.embed',return_value=[[1.,0.],[0.,1.]]):library.semantic.build(library)
        with patch.object(SemanticIndex,'model_identity',side_effect=OSError('offline')):
            self.assertEqual(library.retrieve('Lubricate bearings','1')[0]['page'],2)

    def test_new_revision_replaces_old_followup_sources(self):
        library=self.seed()
        with library.connect() as db:
            db.execute("INSERT INTO manuals VALUES(2,'Pump guide','pump.txt','two',2,'two',1,'now','fixture')")
            db.execute("INSERT INTO chunks VALUES(3,2,1,'Bearings must be lubricated weekly.')")
        sources=assistant.manual_context('bearings',library,[],[1])
        self.assertEqual({s['manual_id'] for s in sources},{2})

    def test_gpu_wait_can_be_cancelled(self):
        control=RunControl();errors=[]
        def wait():
            try:
                with model_runtime.gpu_slot(control):pass
            except Cancelled:errors.append('stopped')
        with model_runtime.gpu_slot():
            thread=threading.Thread(target=wait);thread.start();control.cancel();thread.join(2)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,['stopped'])

    def test_metrics_store_counts_without_prompt_or_response(self):
        runtime=model_runtime.runtime(self.root)
        runtime.record('code','fixture',time.monotonic(),{'prompt_eval_count':30,'eval_count':20,'message':{'content':'private fixture'}})
        self.assertEqual(runtime.recent()[0][3:5],(30,20))
        self.assertNotIn(b'private fixture',(self.root/'nova-metrics.sqlite3').read_bytes())

    def test_exact_edit_handles_windows_newlines_without_changing_encoding(self):
        for encoding,bom in [('utf-8',b''),('utf-8',b'\xef\xbb\xbf'),('utf-16-le',b'\xff\xfe'),('utf-16-be',b'\xfe\xff')]:
            original='first\r\nsecond\r\nthird\r\n'
            target=self.root/'windows.txt';target.write_bytes(bom+original.encode(encoding))
            codex_agent._tool_edit({'path':'windows.txt','old':'first\nsecond','new':'changed\nsecond'},self.root,lambda _:False)
            self.assertEqual(target.read_bytes(),bom+original.replace('first','changed').encode(encoding))

    def test_file_edit_footer_reports_verification_from_actual_tool_results(self):
        (self.root/'a.txt').write_text('old')
        replies=iter([{'tool':'edit','path':'a.txt','old':'old','new':'new'}, {'final':'All tests passed.'}])
        final,_,_=codex_agent.run('edit',self.root,lambda *a,**k:{'message':{'content':json.dumps(next(replies))}},lambda _:False)
        self.assertIn('0/1 changed files re-read',final)
        self.assertIn('No successful build/test',final)

    def test_incomplete_final_gets_one_bounded_repair(self):
        replies=iter([{'final':'The result is ValueError('},{'final':'The file was inspected; no edits were made.'}])
        final,_,_=codex_agent.run('inspect',self.root,lambda *a,**k:{'message':{'content':json.dumps(next(replies))}},lambda _:False)
        self.assertIn('no edits',final)


class UpgradeUITests(unittest.TestCase):
    def test_coding_context_restores_after_gui_restart_and_undo_clears_it(self):
        from test_codex_stop_undo import temporary_project,isolated_app,wait_app,scripted
        with temporary_project() as tmp:
            root=Path(tmp);project=root/'project';project.mkdir();(project/'a.txt').write_text('old')
            with isolated_app(root) as app:
                app.codex_workdir=project;assistant.save_settings(codex_workdir=str(project))
                app.toggle_codex();tid=app.active_chat_id
                answers=scripted([{'tool':'read','path':'a.txt'}, {'tool':'edit','path':'a.txt','old':'old','new':'new'}, {'final':'Changed a.txt.'}])
                app._codex_chat=lambda *a,**k:answers()
                app.question.insert(0,'Change old to new');app.answer();wait_app(app,lambda:not app.busy)
            with isolated_app(root) as reopened:
                reopened.load_chat(tid)
                if not reopened.codex_mode.get():reopened.toggle_codex()
                captured=[]
                def chat(messages,*args,**kwargs):
                    captured.append(json.dumps(messages));return {'message':{'content':'{"final":"a.txt was changed."}'}}
                reopened._codex_chat=chat
                reopened.question.insert(0,'What did you change?');reopened.answer();wait_app(reopened,lambda:not reopened.busy)
                self.assertIn('Change old to new',captured[0]);self.assertIn('Changed a.txt.',captured[0])
                with patch.object(assistant.messagebox,'askyesno',return_value=True):
                    reopened.codex_undo_button.invoke();wait_app(reopened,lambda:not reopened.busy)
                self.assertIsNone(reopened.agent_state.load(tid,project))
                self.assertEqual((project/'a.txt').read_text(),'old')

    def test_manual_retrieval_runs_off_ui_thread_and_profile_persists(self):
        from test_codex_stop_undo import temporary_project,isolated_app,wait_app
        with temporary_project() as tmp:
            root=Path(tmp)
            with isolated_app(root) as app:
                app.model_profile.set('Fast')
                # Real UI event, not a direct config write.
                def find(widget):
                    result=[]
                    for child in widget.winfo_children():
                        if isinstance(child,assistant.ttk.Combobox) and str(child.cget('textvariable'))==str(app.model_profile):result.append(child)
                        result.extend(find(child))
                    return result
                chooser=find(app.settings_win)[0];chooser.event_generate('<<ComboboxSelected>>')
                app.root.update()
                self.assertEqual(model_runtime.settings(root)['profile'],'Fast')
                beats=[]
                def retrieve(*args):time.sleep(.3);return None
                with patch.object(assistant,'manual_context',side_effect=retrieve),patch.object(assistant,'command_or_respond',return_value='Fixture answer.'):
                    app.question.insert(0,'fixture question');app.answer()
                    app.root.after(30,lambda:beats.append(app.busy))
                    wait_app(app,lambda:not app.busy)
                self.assertEqual(beats,[True])
                self.assertEqual(app.question.get(),'')
            with isolated_app(root) as reopened:self.assertEqual(reopened.model_profile.get(),'Fast')


if __name__=='__main__':unittest.main()
