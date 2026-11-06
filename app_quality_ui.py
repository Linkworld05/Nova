# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only acceptance reports from the trusted quality controller."""
import json
from pathlib import Path
import sqlite3
from contextlib import closing
from datetime import datetime
from tkinter import ttk
import ui_theme


def build_quality_tab(app,tabs,menu):
    root=app.agent_state.path.parent
    page=ttk.Frame(tabs,padding=14);tabs.add(page,text='App checks')
    menu.add_command(label='App checks',command=lambda:tabs.select(page))
    ttk.Label(page,text='Requirements, tests and completion status',font=('Segoe UI',14,'bold')).pack(anchor='w')
    ttk.Label(page,text='A startup or a model saying “done” is insufficient. These reports show actual checks, unresolved requirements and review findings.',wraplength=940).pack(anchor='w',pady=8)
    list_frame=ttk.Frame(page);list_frame.pack(fill='x')
    tree=ttk.Treeview(list_frame,columns=('time','project','status'),show='headings',height=5)
    for key,label,width in [('time','Updated',150),('project','Project',450),('status','Status',130)]:tree.heading(key,text=label);tree.column(key,width=width)
    list_scroll=ttk.Scrollbar(list_frame,orient='vertical',command=tree.yview);tree.configure(yscrollcommand=list_scroll.set)
    list_scroll.pack(side='right',fill='y');tree.pack(side='left',fill='x',expand=True);app.quality_tree=tree
    detail_frame=ttk.Frame(page);detail_frame.pack(fill='both',expand=True,pady=10)
    details=ui_theme.text_widget(detail_frame,wrap='word',height=15,state='disabled')
    detail_scroll=ttk.Scrollbar(detail_frame,orient='vertical',command=details.yview);details.configure(yscrollcommand=detail_scroll.set)
    detail_scroll.pack(side='right',fill='y');details.pack(side='left',fill='both',expand=True);app.quality_details=details
    rows={}
    def show(event=None):
        selection=tree.selection()
        if not selection or selection[0] not in rows:return
        row=rows[selection[0]];data=json.loads(row['evidence']);contract=data.get('contract') or {};checks=data.get('checks') or {};review=data.get('review') or {}
        lines=['Status: '+row['status'],'Project: '+row['project'],'','REQUEST',row['request'],'','REQUIREMENTS']
        criteria={v['id']:v for v in contract.get('items',[])}
        for requirement in data.get('requirements',[]):
            item=criteria.get(requirement['id'],{})
            lines.extend([requirement['id']+': '+requirement['text'],'  Acceptance: '+item.get('acceptance','Not planned'),
                          '  Tests: '+(', '.join(item.get('tests',[])) or 'Not mapped')+(' — manual verification pending' if item.get('manual') else '')])
        lines.extend(['','OBSERVED TESTS'])
        for test in checks.get('tests',[]):
            lines.append(test['status'].upper()+': '+test['id']+' (app lines '+str(test.get('app_lines',0))+', assertions '+str(test.get('meaningful_assertions',0))+')')
            if test.get('details'):lines.append(test['details'][-2200:])
        lines.extend(['','STARTUP',json.dumps(checks.get('launch',{}),ensure_ascii=False),'','REVIEW',json.dumps(review,ensure_ascii=False,indent=2)])
        if data.get('test_generation'):lines.extend(['','TEST GENERATION',json.dumps(data['test_generation'],ensure_ascii=False,indent=2)])
        if checks.get('issues'):lines.extend(['','RUNNER ISSUES',*checks['issues']])
        details.configure(state='normal');details.delete('1.0','end');details.insert('1.0','\n'.join(lines));details.configure(state='disabled')
    def refresh():
        tree.delete(*tree.get_children());rows.clear();path=root/'nova-quality.sqlite3'
        if not path.exists():
            details.configure(state='normal');details.delete('1.0','end');details.insert('1.0','No application acceptance checks have run yet. Reports appear after Nova builds or fixes an app.');details.configure(state='disabled');return
        try:
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
                db.row_factory=sqlite3.Row
                records=db.execute('SELECT id,project,updated,status,request,evidence FROM runs ORDER BY updated DESC LIMIT 50').fetchall()
            for row in records:
                rows[row['id']]=dict(row);tree.insert('','end',iid=row['id'],values=(datetime.fromtimestamp(row['updated']).strftime('%Y-%m-%d %H:%M'),row['project'],row['status']))
            if records:tree.selection_set(records[0]['id']);show()
        except (sqlite3.Error,OSError,ValueError) as exc:
            details.configure(state='normal');details.delete('1.0','end');details.insert('1.0','Could not read local quality reports: '+type(exc).__name__);details.configure(state='disabled')
    tree.bind('<<TreeviewSelect>>',show)
    ttk.Button(page,text='Refresh reports',command=refresh).pack(anchor='w')
    refresh()
