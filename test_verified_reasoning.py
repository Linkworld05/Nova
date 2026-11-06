# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deterministic verification, feedback loops, solver cross-checks and real Tk flow."""
import itertools
import json
from pathlib import Path
import random
import tempfile
import unittest
from unittest.mock import patch
import assistant
import verified_reasoning as reasoning
from verified_tools import calculate,number,schedule,verify_schedule
from tools.benchmark_nova_profiles import CASES


def fixture():
    return reasoning.parse_schedule(CASES[1][1])


class VerificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent,prefix='nova-reasoning-check-')
        self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()

    def test_exact_arithmetic_and_bounded_inputs(self):
        self.assertEqual(calculate('0.1+0.2')['exact'],'3/10')
        self.assertEqual(calculate('(final-initial)/initial*100',{'final':'240','initial':'250'})['value'],'-4')
        self.assertEqual(calculate('1/3')['value'],'1/3')
        for value in ('1e999999999','nan',True,'999999999999999999999999999999999999'):
            with self.assertRaises(ValueError):number(value)

    def test_expressions_cannot_execute_code_or_expand_unbounded(self):
        for expression in ('__import__("os").system("echo bad")','[x for x in range(9)]','(lambda:1)()','a.b','2**999','1/0'):
            with self.subTest(expression=expression),self.assertRaises(ValueError):calculate(expression)
        with self.assertRaises(ValueError):calculate('2+2',[])
        self.assertIn('no está definida',reasoning.respond('Calcula 1/0',self.root))

    def test_percentage_regression_and_unseen_values(self):
        with patch.object(reasoning,'model_json') as model:
            answer=reasoning.respond(CASES[0][1],self.root)
            self.assertIn('240 litros',answer);self.assertIn('-4%',answer)
            self.assertIn('(240-250)/250*100 = -4%',answer)
            unseen=reasoning.respond('Un depósito contiene 680 litros. Aumenta 15% y después disminuye 10%. Calcula el resultado.',self.root)
            self.assertIn('703.8 litros',unseen);self.assertIn('3.5%',unseen)
            model.assert_not_called()

    def test_percent_of_and_decimal_comma(self):
        self.assertIn('50',reasoning.respond('¿Cuánto es 20% de 250?',self.root))
        self.assertIn('3.5',reasoning.respond('Calcula 1,5+2',self.root))

    def test_percentage_increment_is_distinct_from_new_total(self):
        answer=reasoning.respond('Un producto cuesta 250 pesos y aumenta 20%. ¿Cuánto es el aumento?',self.root)
        self.assertIn('**50 pesos**',answer)
        self.assertIsNone(reasoning.native_calculation('El precio es 120 después de aumentar 20%. ¿Cuál era el precio inicial?'))

    def test_task_parser_does_not_silently_drop_a_missing_unit(self):
        with self.assertRaises(ValueError):reasoning.parse_schedule('Hay dos máquinas idénticas. A=2, B=3 h.')

    def test_zero_base_percentage_has_no_division_by_zero(self):
        answer=reasoning.respond('Un depósito contiene 0 litros. Aumenta 20% y disminuye 10%.',self.root)
        self.assertIn('base inicial de cero',answer)

    def test_normal_chat_manual_and_explicit_memories_keep_existing_routes(self):
        for question in ('Hola Nova','Recuerda que 2+2=4','Según el manual calcula 20% de 250','¿Qué es una inteligencia artificial?','¿Por qué 2+2=4?'):
            self.assertIsNone(reasoning.route(question))

    def test_source_parser_preserves_tasks_and_dependencies(self):
        p=fixture();self.assertEqual(p['machines'],2)
        self.assertEqual({t['id']:t['after'] for t in p['tasks']},{'A':[],'B':[],'C':[],'D':['A','B'],'E':['C','D']})

    def test_parser_refuses_ambiguous_or_unsupported_constraints(self):
        for question in ('Dos máquinas distintas. A=2 h, B=3 h.',
                         'Dos máquinas idénticas. A=2 h, B=30 min.',
                         'Dos máquinas idénticas. A=2 h, B=3 h. B requiere A o Z.',
                         'Dos máquinas idénticas. A=2 h, B=3 h. B empieza después de A.',
                         'Dos máquinas idénticas. A=2 h, B=3 h. A solo puede usar máquina 1.'):
            with self.subTest(question=question),self.assertRaises(ValueError):reasoning.parse_schedule(question)

    def test_exact_solver_fixes_previous_failure(self):
        p=fixture();result=schedule(p['machines'],p['tasks'])
        self.assertTrue(result['optimal']);self.assertEqual(result['makespan'],'8')
        self.assertTrue(verify_schedule(p['machines'],p['tasks'],result['entries'])['valid'])

    def test_schedule_verifier_catches_duplicate_wrong_duration_dependency_and_overlap(self):
        p=fixture();entries=[{'task':'A','machine':1,'start':'0','end':'2'}, {'task':'B','machine':2,'start':'0','end':'3'},
                            {'task':'C','machine':2,'start':'3','end':'5'}, {'task':'D','machine':1,'start':'3','end':'7'},
                            {'task':'E','machine':1,'start':'7','end':'8'}, {'task':'E','machine':2,'start':'7','end':'8'}]
        result=verify_schedule(2,p['tasks'],entries)
        self.assertFalse(result['valid']);self.assertTrue(any('exactly once; found 2' in e for e in result['errors']))
        entries[3]=dict(entries[3],start='1',end='6')
        result=verify_schedule(2,p['tasks'],entries)
        for fragment in ('wrong duration','dependency','overlap'):self.assertTrue(any(fragment in e for e in result['errors']))

    def test_missing_task_unknown_machine_and_cycle_rejected(self):
        p=fixture();self.assertFalse(verify_schedule(2,p['tasks'],[])['valid'])
        bad=[dict(p['tasks'][0],after=['A'])]
        with self.assertRaisesRegex(ValueError,'Cyclic'):schedule(2,bad)
        with self.assertRaises(ValueError):schedule(True,p['tasks'])

    def test_fractional_durations_preserved_exactly(self):
        tasks=[{'id':'A','duration':'1/3','after':[]},{'id':'B','duration':'2/3','after':['A']}]
        result=schedule(1,tasks)
        self.assertEqual(result['makespan'],'1');self.assertTrue(verify_schedule(1,tasks,result['entries'])['valid'])

    def test_solver_matches_independent_exhaustive_search(self):
        rng=random.Random(31)
        for _ in range(5):
            tasks=[{'id':name,'duration':str(rng.randint(1,4)),'after':[]} for name in 'ABCD']
            tasks[3]['after']=['A','B'];best=10**9
            for assignment in itertools.product(range(2),repeat=4):
                for order in itertools.permutations(range(4)):
                    end={};available=[0,0]
                    for i in order:
                        task=tasks[i]
                        if any(dep not in end for dep in task['after']):break
                        start=max([available[assignment[i]]]+[end[dep] for dep in task['after']])
                        end[task['id']]=start+int(task['duration']);available[assignment[i]]=end[task['id']]
                    else:best=min(best,max(end.values()))
            self.assertEqual(number(schedule(2,tasks)['makespan']),best)

    def test_concrete_feedback_corrects_duplicate_schedule(self):
        p=fixture();correct=schedule(2,p['tasks'])['entries'];bad=correct+[next(e for e in correct if e['task']=='E')]
        messages=[];responses=iter([{'entries':bad},{'entries':correct}]);events=[]
        def model(root,msgs,*args,**kwargs):messages.append(json.dumps(msgs));return next(responses)
        with patch.object(reasoning,'model_json',side_effect=model):answer=reasoning.respond(CASES[1][1],self.root,observer=events.append)
        self.assertEqual(len(messages),2);self.assertIn('exactly once; found 2',messages[1])
        self.assertTrue(events[-1]['verified']);self.assertIn('**8 h**',answer)

    def test_two_bad_drafts_fall_back_to_checked_cpu_schedule(self):
        events=[]
        with patch.object(reasoning,'model_json',return_value={'entries':[]}) as model:
            answer=reasoning.respond(CASES[1][1],self.root,observer=events.append)
        self.assertEqual(model.call_count,2)
        self.assertEqual(events[-1].get('fallback'),'verified_cpu_schedule');self.assertIn('**8 h**',answer)

    def test_model_outage_does_not_discard_cpu_solution(self):
        with patch.object(reasoning,'model_json',side_effect=OSError('offline')):
            self.assertIn('**8 h**',reasoning.respond(CASES[1][1],self.root))

    def test_calculation_feedback_rejects_invented_inputs(self):
        q='Tengo 3 piezas rojas y 4 azules. Calcula cuántas piezas hay.'
        bad={'quantities':[{'name':'a','value':'9','evidence':'3 piezas'}],'steps':[{'name':'result','expression':'a'}],'unit':'piezas'}
        good={'quantities':[{'name':'a','value':'3','evidence':'3 piezas'},{'name':'b','value':'4','evidence':'4 azules'}],
              'steps':[{'name':'result','expression':'a+b'}],'unit':'piezas'}
        with patch.object(reasoning,'model_json',side_effect=[bad,good]) as model:answer=reasoning.respond(q,self.root)
        self.assertEqual(model.call_count,2);self.assertIn('**7 piezas**',answer)

    def test_protocol_and_agent_expose_cpu_tools_without_commands(self):
        import agent_protocol,codex_agent
        agent_protocol.validate({'tool':'calculate','expression':'2+2'})
        self.assertEqual(json.loads(codex_agent._tool_calculate({'expression':'2+2'},self.root,lambda _:False))['value'],'4')
        p=fixture();agent_protocol.validate({'tool':'schedule','machines':2,'tasks':p['tasks']})
        with self.assertRaises(ValueError):agent_protocol.validate({'tool':'calculate','expression':'__import__("os")'})

    def test_real_gui_send_uses_verified_calculator_and_preserves_draft(self):
        from test_codex_stop_undo import isolated_app,wait_app
        with isolated_app(self.root) as app,patch.object(reasoning,'model_json') as model:
            app.question.insert(0,CASES[0][1]);app.answer()
            app.question.insert(0,'Next draft')
            wait_app(app,lambda:not app.busy)
            answer=app.chat_store.turns(app.active_chat_id)[-1][3]
            self.assertIn('240 litros',answer);self.assertEqual(app.question.get(),'Next draft');model.assert_not_called()


if __name__=='__main__':unittest.main()
