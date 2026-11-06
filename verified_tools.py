# SPDX-License-Identifier: AGPL-3.0-or-later
"""Bounded CPU arithmetic and identical-machine scheduling, independently checked."""
import ast
from collections import Counter
from fractions import Fraction
from decimal import Decimal,localcontext
from math import lcm
import re
import time

NAME=re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,31}$')


def number(value):
    if isinstance(value,bool) or not isinstance(value,(int,float,str)) or len(str(value))>64:
        raise ValueError('Expected a bounded number, not code or a boolean.')
    raw=str(value).replace(',','.')
    if not re.fullmatch(r'[+-]?(?:\d{1,16}(?:\.\d{1,16})?|\.\d{1,16})(?:[eE][+-]?\d{1,3})?|[+-]?\d{1,16}/\d{1,16}',raw):
        raise ValueError('Invalid or excessive numeric literal.')
    try:value=Fraction(raw)
    except (ValueError,ZeroDivisionError):raise ValueError('Invalid numeric value.') from None
    if abs(value)>10**12 or value.denominator.bit_length()>128:
        raise ValueError('Numeric value exceeds the supported limits.')
    return value


def text(value):
    value=Fraction(value)
    if value.denominator==1:return str(value.numerator)
    denominator=value.denominator
    for factor in (2,5):
        while denominator%factor==0:denominator//=factor
    if denominator!=1:return str(value)
    with localcontext() as context:
        context.prec=2000
        return format(Decimal(value.numerator)/Decimal(value.denominator),'f').rstrip('0').rstrip('.')


def calculate(expression,variables=None):
    """Evaluate an arithmetic AST. Never eval(), execute names, or call functions."""
    if not isinstance(expression,str) or not 1<=len(expression)<=300:
        raise ValueError('Expression must contain 1–300 characters.')
    variables={} if variables is None else variables
    if not isinstance(variables,dict) or len(variables)>20 or any(not isinstance(k,str) or not NAME.fullmatch(k) for k in variables):
        raise ValueError('Invalid variable names.')
    values={k:number(v) for k,v in variables.items()}
    try:tree=ast.parse(expression.strip(),mode='eval')
    except (SyntaxError,RecursionError):raise ValueError('Invalid arithmetic expression.') from None
    if len(list(ast.walk(tree)))>100:raise ValueError('Expression is too complex.')
    def visit(node):
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):
            return number(ast.get_source_segment(expression.strip(),node))
        if isinstance(node,ast.Name) and node.id in values:return values[node.id]
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,(ast.UAdd,ast.USub)):
            value=visit(node.operand);return value if isinstance(node.op,ast.UAdd) else -value
        if isinstance(node,ast.BinOp):
            a,b=visit(node.left),visit(node.right)
            if isinstance(node.op,ast.Add):value=a+b
            elif isinstance(node.op,ast.Sub):value=a-b
            elif isinstance(node.op,ast.Mult):value=a*b
            elif isinstance(node.op,ast.Div):
                if not b:raise ValueError('Division by zero.')
                value=a/b
            elif isinstance(node.op,ast.Mod):
                if not b:raise ValueError('Division by zero.')
                value=a%b
            elif isinstance(node.op,ast.Pow):
                if b.denominator!=1 or abs(b)>12 or not a and b<0:raise ValueError('Exponent must be an integer between -12 and 12.')
                value=a**int(b)
            else:raise ValueError('Unsupported arithmetic operator.')
            if value.numerator.bit_length()>2048 or value.denominator.bit_length()>2048:
                raise ValueError('Intermediate result exceeds the limit.')
            return value
        raise ValueError('Only arithmetic, numeric constants and declared variables are allowed.')
    value=visit(tree.body)
    return {'expression':expression,'value':text(value),'exact':str(value)}


def problem(machines,tasks):
    if type(machines) is not int or not 1<=machines<=4:raise ValueError('Use 1–4 identical machines.')
    if not isinstance(tasks,list) or not 1<=len(tasks)<=12:raise ValueError('Use 1–12 tasks.')
    normalized=[];ids=set()
    for task in tasks:
        if not isinstance(task,dict) or set(task)-{'id','duration','after'}:raise ValueError('Task needs id, duration and after.')
        name=task.get('id')
        if not isinstance(name,str) or not NAME.fullmatch(name) or name in ids:raise ValueError('Task IDs must be unique short names.')
        duration=number(task.get('duration'))
        if duration<=0:raise ValueError('Task durations must be positive.')
        after=task.get('after',[])
        if not isinstance(after,list) or any(not isinstance(v,str) for v in after) or len(set(after))!=len(after):raise ValueError('Dependencies must be a list of unique task IDs.')
        normalized.append({'id':name,'duration':duration,'after':list(after)});ids.add(name)
    by_id={t['id']:t for t in normalized};visiting=set();done=set()
    def walk(name):
        if name not in ids:raise ValueError('Unknown dependency '+name)
        if name in visiting:raise ValueError('Cyclic task dependencies.')
        if name in done:return
        visiting.add(name)
        for dep in by_id[name]['after']:walk(dep)
        visiting.remove(name);done.add(name)
    for name in ids:walk(name)
    return normalized


def verify_schedule(machines,tasks,entries):
    tasks=problem(machines,tasks);expected={t['id']:t for t in tasks}
    if not isinstance(entries,list) or len(entries)>30:raise ValueError('Schedule must be a bounded list.')
    errors=[];parsed=[]
    for entry in entries:
        if not isinstance(entry,dict) or set(entry)!={'task','machine','start','end'}:
            errors.append('Each entry needs task, machine, start and end.');continue
        if not isinstance(entry['task'],str) or entry['task'] not in expected:
            errors.append('Unknown task '+str(entry['task']));continue
        try:start,end=number(entry['start']),number(entry['end'])
        except ValueError as exc:errors.append(str(exc));continue
        machine=entry['machine']
        if type(machine) is not int or not 1<=machine<=machines:
            errors.append('Task '+entry['task']+' uses a machine outside 1..'+str(machines));continue
        if start<0:errors.append('Task '+entry['task']+' starts before zero.')
        if end-start!=expected[entry['task']]['duration']:errors.append('Task '+entry['task']+' has the wrong duration.')
        parsed.append(dict(entry,start=start,end=end))
    counts=Counter(e['task'] for e in parsed)
    for name in expected:
        if counts[name]!=1:errors.append('Task '+name+' must occur exactly once; found '+str(counts[name])+'.')
    lookup={e['task']:e for e in parsed}
    for entry in parsed:
        for dep in expected[entry['task']]['after']:
            if dep in lookup and entry['start']<lookup[dep]['end']:errors.append('Task '+entry['task']+' starts before dependency '+dep+' ends.')
    for machine in range(1,machines+1):
        rows=sorted((e for e in parsed if e['machine']==machine),key=lambda e:e['start'])
        for left,right in zip(rows,rows[1:]):
            if left['end']>right['start']:errors.append('Tasks '+left['task']+' and '+right['task']+' overlap on machine '+str(machine)+'.')
    return {'valid':not errors,'errors':list(dict.fromkeys(errors)),
            'makespan':text(max((e['end'] for e in parsed),default=Fraction(0)))}


def schedule(machines,tasks,time_limit=2):
    """MILP on CPU, followed by an independent exact-fraction feasibility check."""
    import numpy as np
    from scipy.optimize import Bounds,LinearConstraint,milp
    started=time.monotonic();tasks=problem(machines,tasks)
    scale=1
    for task in tasks:scale=lcm(scale,task['duration'].denominator)
    horizon=sum(t['duration'] for t in tasks)*scale
    if scale>1000 or horizon>100000:raise ValueError('Choose consistent units with at most three decimal places and a smaller time horizon.')
    horizon=int(horizon);n=len(tasks);pairs=[(i,j) for i in range(n) for j in range(i+1,n)]
    assignment=lambda i,m:n+1+i*machines+m
    order={pair:n+1+n*machines+k for k,pair in enumerate(pairs)}
    size=n+1+n*machines+len(pairs);rows=[];lower=[];upper=[]
    def add(values,lo=-np.inf,hi=np.inf):
        row=np.zeros(size)
        for index,value in values.items():row[index]=value
        rows.append(row);lower.append(lo);upper.append(hi)
    positions={t['id']:i for i,t in enumerate(tasks)}
    for i,task in enumerate(tasks):
        duration=int(task['duration']*scale)
        add({assignment(i,m):1 for m in range(machines)},1,1)
        add({i:1,n:-1},hi=-duration)
        for dep in task['after']:
            j=positions[dep];add({j:1,i:-1},hi=-int(tasks[j]['duration']*scale))
    for i,j in pairs:
        y=order[i,j]
        for m in range(machines):
            add({i:1,j:-1,assignment(i,m):horizon,assignment(j,m):horizon,y:horizon},hi=3*horizon-int(tasks[i]['duration']*scale))
            add({j:1,i:-1,assignment(i,m):horizon,assignment(j,m):horizon,y:-horizon},hi=2*horizon-int(tasks[j]['duration']*scale))
    # Machines are interchangeable; fixing one assignment removes symmetric solutions.
    add({assignment(0,0):1},1,1)
    high=np.ones(size);high[:n+1]=horizon
    objective=np.zeros(size);objective[n]=1
    result=milp(objective,integrality=np.ones(size),bounds=Bounds(np.zeros(size),high),
                constraints=LinearConstraint(np.array(rows),np.array(lower),np.array(upper)),
                options={'time_limit':max(.01,min(float(time_limit),5)),'mip_rel_gap':0,'node_limit':100000})
    if result.x is None:raise ValueError('No verified schedule found within the solver budget.')
    if any(abs(v-round(v))>1e-5 for v in result.x):raise ValueError('Solver returned a nonintegral candidate.')
    entries=[]
    for i,task in enumerate(tasks):
        start=Fraction(round(result.x[i]),scale)
        machine=next((m+1 for m in range(machines) if result.x[assignment(i,m)]>.5),None)
        entries.append({'task':task['id'],'machine':machine,'start':text(start),'end':text(start+task['duration'])})
    clean=[dict(t,duration=str(t['duration'])) for t in tasks]
    checked=verify_schedule(machines,clean,entries)
    if not checked['valid']:raise ValueError('Solver output failed independent verification: '+'; '.join(checked['errors']))
    return {'entries':entries,'makespan':checked['makespan'],'optimal':result.status==0,'verified':True,
            'seconds':round(time.monotonic()-started,5),'solver_status':int(result.status)}
