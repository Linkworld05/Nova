# SPDX-License-Identifier: AGPL-3.0-or-later
"""Automatic CPU-backed reasoning with bounded, concrete model feedback."""
import ast
from fractions import Fraction
import json
import re
import time
import unicodedata
import model_runtime
from verified_tools import calculate,number,text,schedule,verify_schedule

NUM=r'[-+]?(?:\d+(?:[.,]\d+)?|[.,]\d+)'
SCHEDULE_SCHEMA={'type':'object','properties':{'entries':{'type':'array','maxItems':12,'items':{'type':'object','properties':{
    'task':{'type':'string'},'machine':{'type':'integer'},'start':{'type':'string'},'end':{'type':'string'}},
    'required':['task','machine','start','end'],'additionalProperties':False}}},'required':['entries'],'additionalProperties':False}
CALC_SCHEMA={'type':'object','properties':{
    'quantities':{'type':'array','maxItems':12,'items':{'type':'object','properties':{'name':{'type':'string'},'value':{'type':'string'},'evidence':{'type':'string'}},'required':['name','value','evidence'],'additionalProperties':False}},
    'steps':{'type':'array','maxItems':8,'items':{'type':'object','properties':{'name':{'type':'string'},'expression':{'type':'string'}},'required':['name','expression'],'additionalProperties':False}},
    'unit':{'type':'string'}},'required':['quantities','steps','unit'],'additionalProperties':False}


def plain(value):return ''.join(c for c in unicodedata.normalize('NFKD',value.lower()) if not unicodedata.combining(c))
def spanish(question):return bool(re.search(r'[¿áéíóúñ]|\b(?:cuanto|calcula|tareas|maquinas|litros|deposito|resuelve)\b',plain(question)))


def clean_question(question):
    return re.sub(r'(?i)(?:un\s+)?m[aá]ximo\s+(?:de\s+)?\d+\s+palabras|(?:at most|max(?:imum)?)\s+\d+\s+words','',question)


def route(question):
    if len(question)>10000:return None
    q=plain(clean_question(question))
    if re.match(r'^\W*(?:por que|why|demuestra|demostracion|prove|explain the concept)\b',q):return None
    if re.search(r'\b(recuerda|remember|olvida|forget|remind|recuerdame|manual(?:es)?|pdf|documentation)\b',q):return None
    if '```' in q or re.search(r'https?://',q):return None
    if re.search(r'\b(maquinas?|machines?)\b',q) and re.search(r'\b(tareas?|tasks?|requiere|depends|requires)\b',q) and re.search(r'\b[A-Za-z]\w*\s*[:=]\s*\d',q):return 'schedule'
    if re.search(r'\d\s*[-+*/^]\s*\(?\s*[-+]?\d',q):return 'calculate'
    if re.search(r'\d\s*(?:%|por ciento|percent)',q) and re.search(r'\b(aument\w*|dismin\w*|reduc\w*|sube|baja|increment\w*|increase\w*|decrease\w*|drops?|rises?|cuanto|what|calcula\w*)\b',q):return 'calculate'
    if len(re.findall(NUM,q))>=2 and re.search(r'\b(calcula\w*|calculate|resuelve|evaluate|evalua)\b',q):return 'calculate'
    return None


def native_calculation(question):
    q=clean_question(question);p=plain(q)
    expression=re.sub(r'^(?:\s*[¿?]?\s*)(?:calcula|calculate|resuelve|eval[uú]a|evaluate|cu[aá]nto es|what is)?\s*','',q,flags=re.I).strip(' ?¿=.')
    if re.fullmatch(r'[\d\s.,+*/%()^\-]+',expression) and any(c in expression for c in '+-*/%^'):
        expression=expression.replace('^','**').replace(',','.')
        result=calculate(expression)
        return {'kind':'arithmetic','steps':[result],'unit':''}
    percentages=list(re.finditer(r'('+NUM+r')\s*(?:%|por ciento\b|percent\b)',p))
    if not percentages:return None
    if len(percentages)>8:raise ValueError('Use at most eight percentage operations per request.')
    all_numbers=list(re.finditer(NUM,p))
    seeds=[m for m in all_numbers if not any(v.start()<=m.start()<v.end() for v in percentages)]
    if len(seeds)!=1:return None
    initial=number(seeds[0].group());value=initial;steps=[];end=0
    if re.search(r'\b(?:cual|cuanto|what|find|calcula).{0,50}\b(?:original|inicial|before|antes)\b',p):return None
    amount_question=re.search(r'\b(?:cuanto (?:es|representa|vale)|cual es (?:el|la))\s+(?:el |la |ese |esa )?(?:aumento|incremento|descuento|reduccion|variacion)\b',p)
    if amount_question:
        if len(percentages)!=1:return None
        result=calculate(f'({text(initial)})*({percentages[0].group(1).replace(",",".")})/100')
        unit=re.match(r'\s*(litros?|liters?|kg|euros?|d[oó]lares?|pesos?|usd|unidades?)\b',q[seeds[0].end():],re.I)
        return {'kind':'arithmetic','steps':[result],'unit':unit[1] if unit else ''}
    for index,match in enumerate(percentages):
        pct=number(match.group(1));section=p[end:match.start()];end=match.end()
        verbs=list(re.finditer(r'\b(aument\w*|increment\w*|sube|increas\w*|rises?|dismin\w*|reduc\w*|baja|decreas\w*|drops?)\b',section))
        if not verbs:
            if len(percentages)!=1 or not re.search(r'\b(?:cuanto|calcula\w*|what|calculate)\b',p):return None
            expression=f'({text(initial)})*({text(pct)})/100'
        else:
            verb=verbs[-1].group();sign='-' if re.match(r'dismin|reduc|baja|decreas|drop',verb) else '+'
            expression=f'({text(value)})*(1{sign}({text(pct)})/100)'
        result=calculate(expression);value=Fraction(result['exact']);steps.append(result)
    unit_match=re.match(r'\s*(litros?|liters?|kg|kilogramos?|euros?|d[oó]lares?|usd|unidades?)\b',q[seeds[0].end():],re.I)
    unit=unit_match.group(1) if unit_match else ''
    changed=bool(re.search(r'\b(aument\w*|dismin\w*|reduc\w*|sube|baja|increas\w*|decreas\w*|drops?|rises?)\b',p))
    change=calculate('(final-initial)/initial*100',{'final':str(value),'initial':str(initial)}) if changed and initial else None
    return {'kind':'percent','steps':steps,'unit':unit,'initial':text(initial),'value':text(value),
            'change':change,'zero_base':bool(changed and not initial)}


def parse_schedule(question):
    q=clean_question(question);p=plain(q)
    if not re.search(r'\b(identicas?|identicos?|identical|equivalentes?)\b',p):raise ValueError('Confirm that the machines are identical.')
    machine=re.search(r'\b(\d+|una?|uno|dos|tres|cuatro|one|two|three|four)\s+(?:maquinas?|machines?)\b',p)
    if not machine:raise ValueError('Specify the number of identical machines.')
    names={'una':1,'un':1,'uno':1,'dos':2,'tres':3,'cuatro':4,'one':1,'two':2,'three':3,'four':4}
    machines=names.get(machine[1],int(machine[1]) if machine[1].isdigit() else None)
    found=list(re.finditer(r'\b([A-Za-z][A-Za-z0-9_]{0,31})\s*[:=]\s*('+NUM+r')\s*(h(?:oras?|ours?)?|min(?:utos?|utes?)?|s(?:egundos?|econds?)?)\b',q,re.I))
    if not found:raise ValueError('Specify task durations, for example A=2 h, B=3 h.')
    declared=list(re.finditer(r'\b([A-Za-z][A-Za-z0-9_]{0,31})\s*[:=]\s*(?=[+-]?(?:\d|[.,]\d))',q))
    if len(declared)!=len(found) or {m[1] for m in declared}!={m[1] for m in found}:raise ValueError('Every task needs an explicit duration and supported time unit, for example A=2 h.')
    units=[plain(m[3]) for m in found]
    normalize=lambda unit:'h' if unit.startswith('h') else 'min' if unit.startswith('min') else 's'
    if len({normalize(v) for v in units})!=1:raise ValueError('Use the same time unit for every task.')
    tasks=[{'id':m[1],'duration':m[2].replace(',','.'),'after':[]} for m in found]
    index={t['id'].casefold():t for t in tasks}
    if len(index)!=len(tasks):raise ValueError('A task duration was defined more than once.')
    clauses=list(re.finditer(r'\b([A-Za-z][A-Za-z0-9_]*)\s+(?:requiere[n]?|depende\s+de|requires?|depends?\s+on)\s+([^.;\n]+)',q,re.I))
    for match in clauses:
        key=match[1].casefold()
        if key not in index:raise ValueError('Dependency given for an unknown task.')
        words=re.findall(r'\b[A-Za-z][A-Za-z0-9_]*\b',plain(match[2]))
        allowed={'y','e','and','terminadas','terminados','terminada','terminado','completadas','completados','completed','finished','to','be'}
        if any(w not in index and w not in allowed for w in words):raise ValueError('Dependency clause is ambiguous; use D requiere A y B. Separate each clause with a period.')
        deps=[index[w]['id'] for w in words if w in index]
        if not deps:raise ValueError('Dependency clause contains no known tasks.')
        index[key]['after']=list(dict.fromkeys(index[key]['after']+deps))
    # Unsupported resource/release/deadline constraints must not be silently ignored.
    if re.search(r'\b(deadline|plazo|descanso|setup|release|antes|despues|after|before|tras|until|solo (?:en|puede)|only on|maquina\s+\d|machine\s+\d)\b',p):raise ValueError('Use explicit dependency clauses (D requiere A y B). Release times, deadlines, setup times and fixed-machine assignments are not supported.')
    if re.search(r'\b(?:depend\w*|requier\w*|requires?)\b',p) and not clauses and not re.search(r'(?:sin dependencias|no dependencies)',p):raise ValueError('Could not read the dependency clauses.')
    from verified_tools import problem
    problem(machines,tasks)
    return {'machines':machines,'tasks':tasks,'unit':normalize(units[0])}


def model_json(root,messages,schema,tokens=1100):
    result=model_runtime.runtime(root).chat(messages,tokens=tokens,allow_thinking=False,response_format=schema)
    value=json.loads(result['message']['content'])
    if not isinstance(value,dict):raise ValueError('Expected a structured object.')
    return value


def grounded_calculation(question,payload):
    if set(payload)!={'quantities','steps','unit'} or not isinstance(payload['quantities'],list) or not 1<=len(payload['quantities'])<=12 or not isinstance(payload['steps'],list) or not 1<=len(payload['steps'])<=8:raise ValueError('Supply 1–12 quoted input quantities and 1–8 calculation steps.')
    values={};permitted={Fraction(0),Fraction(1),Fraction(100)}
    for quantity in payload['quantities']:
        if set(quantity)!={'name','value','evidence'}:raise ValueError('Each input needs name, value and exact evidence.')
        name=quantity['name'];value=number(quantity['value']);evidence=quantity['evidence']
        if not isinstance(name,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}',name) or name in values:raise ValueError('Input names must be unique short identifiers.')
        if not isinstance(evidence,str) or not evidence or evidence not in question or not any(number(m.group())==value for m in re.finditer(NUM,evidence)):
            raise ValueError('Input '+name+' is not backed by an exact quote and number from the question.')
        values[name]=str(value);permitted.add(value)
    steps=[]
    for step in payload['steps']:
        if set(step)!={'name','expression'} or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]{0,31}',str(step['name'])) or step['name'] in values:raise ValueError('Step names must be unique.')
        expression=step['expression'];tree=ast.parse(expression,mode='eval')
        for node in ast.walk(tree):
            if isinstance(node,ast.Constant) and type(node.value) in (int,float) and number(ast.get_source_segment(expression,node)) not in permitted:raise ValueError('Expression contains a number absent from the question; express percentages using /100.')
        result=calculate(expression,values)
        result['display_expression']=re.sub(r'\b[A-Za-z][A-Za-z0-9_]*\b',lambda match:'('+values[match.group()]+')' if match.group() in values else match.group(),expression)
        values[step['name']]=result['exact'];steps.append(result)
    unit=payload['unit']
    if not isinstance(unit,str) or len(unit)>30 or unit and plain(unit) not in plain(question):raise ValueError('Unit is absent from the question.')
    return {'kind':'interpreted','steps':steps,'unit':unit}


def render_calculation(result,is_spanish):
    unit=(' '+result['unit']) if result.get('unit') else ''
    steps=result['steps'];answer=('Resultado' if is_spanish else 'Result')+': **'+steps[-1]['value']+unit+'**.'
    if result['kind']=='interpreted':answer+=('\n\nOperaciones interpretadas y comprobadas:' if is_spanish else '\n\nInterpreted and checked operations:')
    answer+='\n\n'+'\n'.join('`'+r.get('display_expression',r['expression'])+' = '+r['value']+'`' for r in steps)
    if result.get('change'):
        change=result['change'];answer+=('\n\nCambio respecto al inicio: **' if is_spanish else '\n\nChange from the initial value: **')+change['value']+'%**.\n`'+change['expression'].replace('final',result['value']).replace('initial',result['initial'])+' = '+change['value']+'%`'
    if result.get('zero_base'):answer+=('\n\nEl cambio porcentual no está definido con una base inicial de cero.' if is_spanish else '\n\nPercentage change is undefined for a zero starting value.')
    return answer


def render_schedule(problem,result,is_spanish):
    label=('Tiempo mínimo total' if is_spanish else 'Minimum total time') if result['optimal'] else ('Duración de un horario válido; mínimo no demostrado' if is_spanish else 'Feasible schedule duration; optimum not proven')
    answer=label+': **'+result['makespan']+' '+problem['unit']+'**.\n'
    for machine in range(1,problem['machines']+1):
        rows=sorted((e for e in result['entries'] if e['machine']==machine),key=lambda e:number(e['start']))
        answer+='\n**'+('Máquina' if is_spanish else 'Machine')+' '+str(machine)+'**: '+('; '.join(e['task']+' ('+e['start']+'–'+e['end']+' '+problem['unit']+')' for e in rows) or ('sin tareas' if is_spanish else 'idle'))+'.\n'
    answer+=('\nComprobado: cada tarea se ejecuta una vez, con su duración, sus dependencias y sin solapamientos.' if is_spanish else '\nChecked: each task runs once, with the specified duration, dependencies and no overlaps.')
    return answer


def respond(question,root,history=None,observer=None):
    kind=route(question)
    if not kind:return None
    started=time.monotonic();events=[];ok=False
    def emit(event):
        events.append(event)
        if observer:observer(event)
    try:
        if kind=='calculate':
            native=native_calculation(question)
            if native:
                ok=True;emit({'stage':'arithmetic','verified':True,'model_calls':0})
                return render_calculation(native,spanish(question))
            messages=[{'role':'system','content':'Translate the user arithmetic problem into input quantities and expressions, not an answer. Copy exact evidence for each input from the question. Use only + - * / ** % parentheses and named variables. Use /100 for percentages. Do not add unstated assumptions or outside data. Output JSON with quantities, steps, unit.'},{'role':'user','content':question}]
            for attempt in range(2):
                try:
                    payload=model_json(root,messages,CALC_SCHEMA)
                    verified=grounded_calculation(question,payload)
                    ok=True;emit({'stage':'arithmetic','attempt':attempt+1,'verified':True})
                    return render_calculation(verified,spanish(question))
                except (ValueError,KeyError,TypeError,SyntaxError) as exc:
                    emit({'stage':'arithmetic','attempt':attempt+1,'verified':False,'error':str(exc)})
                    messages.append({'role':'user','content':'Validation failed: '+str(exc)+'. Correct the structured calculation; do not invent inputs.'})
        else:
            problem=parse_schedule(question)
            solved=schedule(problem['machines'],problem['tasks'])
            emit({'stage':'solver','verified':True,'optimal':solved['optimal'],'makespan':solved['makespan'],'seconds':solved['seconds']})
            messages=[{'role':'system','content':'Create a nonpreemptive schedule from the supplied problem. Each task occurs EXACTLY ONCE on ONE machine. Machine IDs are 1..machines. No overlaps on each machine. Respect all durations and predecessor completion times. Return only JSON entries with task, machine, start, end; times must be numeric strings.'},
                      {'role':'user','content':json.dumps(dict(problem,target_makespan=solved['makespan']),ensure_ascii=False)}]
            # Bounded correction loop. CPU fallback remains independently verified.
            for attempt in range(2):
                try:
                    payload=model_json(root,messages,SCHEDULE_SCHEMA)
                    if set(payload)!={'entries'}:raise ValueError('Return only schedule entries.')
                    checked=verify_schedule(problem['machines'],problem['tasks'],payload['entries'])
                    errors=checked['errors']
                    if checked['valid'] and solved['optimal'] and number(checked['makespan'])!=number(solved['makespan']):errors=['The schedule is feasible but not minimal; a verified '+solved['makespan']+'-unit schedule exists.']
                    if not errors:
                        solved=dict(solved,entries=payload['entries'],makespan=checked['makespan'])
                        emit({'stage':'schedule','attempt':attempt+1,'verified':True});break
                    emit({'stage':'schedule','attempt':attempt+1,'verified':False,'errors':errors})
                    messages.extend([{'role':'assistant','content':json.dumps(payload)}, {'role':'user','content':'Concrete verification failures: '+json.dumps(errors)+'. Correct them. Each task runs once on one machine. No extra tasks.'}])
                except (ValueError,KeyError,TypeError) as exc:
                    emit({'stage':'schedule','attempt':attempt+1,'verified':False,'error':str(exc)})
                    messages.append({'role':'user','content':'Validation failed: '+str(exc)+'. Correct the JSON.'})
                except Exception as exc:
                    emit({'stage':'schedule','verified':False,'error_type':type(exc).__name__});break
            ok=True
            if not any(e.get('verified') and e.get('stage')=='schedule' for e in events):emit({'stage':'schedule','fallback':'verified_cpu_schedule','verified':True})
            return render_schedule(problem,solved,spanish(question))
    except (ValueError,KeyError,TypeError,SyntaxError) as exc:
        emit({'stage':kind,'verified':False,'error':str(exc)})
        if str(exc)=='Division by zero.':
            ok=True
            return 'La división entre cero no está definida.' if spanish(question) else 'Division by zero is undefined.'
    except Exception as exc:
        emit({'stage':kind,'verified':False,'error_type':type(exc).__name__})
    finally:
        # Content-free telemetry. Never turn unconfirmed corrections into user memories.
        model_runtime.runtime(root).record('verify','cpu:'+kind,started,error=None if ok else 'unverified')
    return ('No puedo verificar ese problema con los datos disponibles. Para horarios, indica máquinas idénticas, duraciones como A=2 h y dependencias como D requiere A y B. Para cálculos, escribe los valores y la operación sin datos implícitos.' if spanish(question) else
            'I cannot verify this problem from the supplied data. For schedules, specify identical machines, durations such as A=2 h and dependencies such as D requires A and B. For calculations, supply the values and operation without unstated assumptions.')
