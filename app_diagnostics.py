# SPDX-License-Identifier: AGPL-3.0-or-later
"""Concrete source locations for repair-loop mistakes; warnings are not proofs."""
import ast
import hashlib
from pathlib import Path


def signature(data):
    try:return hashlib.sha256(ast.dump(ast.parse(data.decode('utf-8-sig')),include_attributes=False).encode()).hexdigest()
    except (SyntaxError,UnicodeError):return hashlib.sha256(data).hexdigest()


def inspect_entry(path):
    path=Path(path)
    if path.suffix.lower()!='.py':return []
    try:tree=ast.parse(path.read_text(encoding='utf-8-sig'))
    except (SyntaxError,UnicodeError) as exc:return [path.name+': '+str(exc)]
    guards=[];definitions={};issues=[]
    for node in tree.body:
        if isinstance(node,ast.If):
            condition=ast.dump(node.test)
            if '__name__' in condition and '__main__' in condition:guards.append(node.lineno)
        if isinstance(node,(ast.FunctionDef,ast.ClassDef)):
            if any(isinstance(d,ast.Name) and d.id=='overload' for d in getattr(node,'decorator_list',[])):continue
            definitions.setdefault(node.name,[]).append(node.lineno)
    if len(guards)>1:issues.append(path.name+' has '+str(len(guards))+' __main__ startup blocks at lines '+', '.join(map(str,guards))+'. They all execute. If output/startup is duplicated, inspect and remove the unintended duplicate block, not another print inside the first block.')
    for name,lines in definitions.items():
        if len(lines)>1:issues.append(path.name+' defines '+name+' more than once at lines '+', '.join(map(str,lines))+'. Later definitions replace earlier ones.')
    calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='isinstance' and len(n.args)>1]
    if any(any(isinstance(v,ast.Name) and v.id=='int' for v in ast.walk(call.args[1])) for call in calls) and not any(isinstance(n,ast.Name) and n.id=='bool' for n in ast.walk(tree)):
        issues.append('Python bool subclasses int: isinstance(True, (int,float)) is True. If booleans must be rejected, check isinstance(value,bool) explicitly before accepting numeric arguments.')
    return issues
