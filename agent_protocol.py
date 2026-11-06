# SPDX-License-Identifier: AGPL-3.0-or-later
"""Validate tool arguments before any side effects, even with a well-formed JSON reply."""
TOOLS = ('list','read','edit','write','run','find','search','remember','http','vps','dbdump','browser','plan','calculate','schedule','verify_schedule','requirements','run_tests','review_app','map_tests')
FIELDS = {
    'list': {'path','limit'}, 'read': {'path','start','lines'},
    'edit': {'path','old','new'}, 'write': {'path','content'},
    'run': {'cmd'}, 'find': {'root','term'}, 'search': {'path','term'},
    'remember': {'text'}, 'http': {'url','method','data'},
    'vps': {'cmd','upload','download','to'}, 'dbdump': {'env'},
    'browser': {'action','url','selector','value','full'}, 'plan': {'steps'},
    'calculate': {'expression','variables'},'schedule': {'machines','tasks','unit'},
    'verify_schedule': {'machines','tasks','entries'},
    'requirements':{'entrypoint','items'},'run_tests':{'framework','start','pattern'},'review_app':set(),
    'map_tests':{'requirement','tests'},
}
REQUIRED = {'read': ('path',), 'edit': ('path','old','new'), 'write': ('path','content'),
            'run': ('cmd',), 'find': ('term',), 'search': ('term',),
            'remember': ('text',), 'http': ('url',), 'plan': ('steps',),
            'calculate':('expression',),'schedule':('machines','tasks'),
            'verify_schedule':('machines','tasks','entries'),'requirements':('entrypoint','items'),'map_tests':('requirement','tests')}


def schema():
    variants=[{'type':'object','properties':{'final':{'type':'string'}},'required':['final'],'additionalProperties':False}]
    for tool in TOOLS:
        properties={'tool':{'const':tool}}
        for key in FIELDS[tool]:
            if key in ('start','lines','limit') and tool!='run_tests':value={'type':'integer','minimum':1,'maximum':10000}
            elif key=='items':value={'type':'array','minItems':1,'maxItems':12,'items':{'type':'object','properties':{'id':{'type':'string'},'acceptance':{'type':'string'},'tests':{'type':'array','items':{'type':'string'}},'manual':{'type':'boolean'}},'required':['id','acceptance','tests','manual'],'additionalProperties':False}}
            elif key=='framework':value={'type':'string','enum':['python','node']}
            elif key=='tests':value={'type':'array','minItems':1,'maxItems':20,'items':{'type':'string'}}
            elif key=='machines':value={'type':'integer','minimum':1,'maximum':4}
            elif key=='variables':value={'type':'object','additionalProperties':{'type':'string'}}
            elif key=='tasks':value={'type':'array','minItems':1,'maxItems':12,'items':{'type':'object','properties':{'id':{'type':'string'},'duration':{'type':'string'},'after':{'type':'array','items':{'type':'string'}}},'required':['id','duration','after'],'additionalProperties':False}}
            elif key=='entries':value={'type':'array','maxItems':30,'items':{'type':'object','properties':{'task':{'type':'string'},'machine':{'type':'integer'},'start':{'type':'string'},'end':{'type':'string'}},'required':['task','machine','start','end'],'additionalProperties':False}}
            elif key=='full':value={'type':'boolean'}
            elif key=='data':value={'type':'object','additionalProperties':True}
            elif key=='steps':value={'type':'array','minItems':1,'maxItems':12,'items':{'type':'object','properties':{'text':{'type':'string'},'status':{'enum':['pending','active','done','blocked']}},'required':['text','status'],'additionalProperties':False}}
            else:value={'type':'string'}
            properties[key]=value
        variants.append({'type':'object','properties':properties,'required':['tool',*REQUIRED.get(tool,())],'additionalProperties':False})
    return {'oneOf':variants}


SCHEMA=schema()


def validate(obj):
    if not isinstance(obj, dict):
        raise ValueError('Reply must be a JSON object.')
    if 'final' in obj:
        if set(obj) != {'final'} or not isinstance(obj['final'], str) or not obj['final'].strip():
            raise ValueError('final must be nonempty text, with no tool action.')
        return
    tool = obj.get('tool')
    if not isinstance(tool, str) or tool not in TOOLS:
        raise ValueError('Unknown tool. Use: '+', '.join(TOOLS))
    extras = set(obj) - FIELDS[tool] - {'tool'}
    if extras:
        raise ValueError('Unexpected argument for '+tool+': '+', '.join(sorted(map(str, extras)))+
                         '. Allowed arguments: '+', '.join(sorted(FIELDS[tool]))+'.')
    for key in REQUIRED.get(tool, ()):
        if key not in obj:
            raise ValueError(tool+' requires '+key)
    for key,value in obj.items():
        if key in ('data','steps','tool','tasks','entries','variables','items','tests'):
            continue
        if key=='machines':
            if type(value) is not int or not 1<=value<=4:raise ValueError('machines must be 1–4.')
            continue
        if key in ('start','lines','limit') and tool!='run_tests':
            if type(value) is not int or not 1 <= value <= 10000:
                raise ValueError(key+' must be an integer between 1 and 10000.')
        elif key == 'full':
            if type(value) is not bool:
                raise ValueError('full must be boolean.')
        elif not isinstance(value,str):
            raise ValueError(key+' must be text.')
        elif not value.strip() and key not in ('new','content','value'):
            raise ValueError(key+' cannot be empty.')
    if tool == 'vps' and sum(bool(obj.get(k)) for k in ('cmd','upload','download')) != 1:
        raise ValueError('vps requires exactly one cmd, upload, or download.')
    if tool == 'browser':
        action = obj.get('action','goto')
        if action not in ('goto','text','click','fill','submit','screenshot'):
            raise ValueError('Unknown browser action.')
        if action == 'goto' and not obj.get('url'):
            raise ValueError('browser goto requires url.')
        if action in ('click','fill','submit') and not obj.get('selector'):
            raise ValueError('browser action requires selector.')
    if tool == 'plan':
        steps = obj['steps']
        if not isinstance(steps,list) or not 1 <= len(steps) <= 12:
            raise ValueError('plan needs 1 to 12 steps.')
        for step in steps:
            if not isinstance(step,dict) or set(step) != {'text','status'} or not isinstance(step['text'],str) or not 1 <= len(step['text']) <= 300 or step['status'] not in ('pending','active','done','blocked'):
                raise ValueError('Each plan step needs text (1-300 chars) and status pending/active/done/blocked.')
    if tool=='calculate':
        from verified_tools import calculate
        calculate(obj['expression'],obj.get('variables'))
    if tool in ('schedule','verify_schedule'):
        from verified_tools import problem,verify_schedule
        problem(obj['machines'],obj['tasks'])
        if tool=='verify_schedule':verify_schedule(obj['machines'],obj['tasks'],obj['entries'])
