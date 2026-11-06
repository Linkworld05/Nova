# SPDX-License-Identifier: AGPL-3.0-or-later
"""Profile selection and local retrieval/latency visibility, without extra services."""
import threading
import tkinter as tk
from tkinter import ttk
import model_runtime
import ui_theme
from semantic_index import SemanticIndex


def build_performance_tab(app, tabs, menu):
    root = app.agent_state.path.parent
    page = ttk.Frame(tabs, padding=16)
    tabs.add(page, text='Performance')
    menu.add_command(label='Performance', command=lambda: tabs.select(page))
    ttk.Label(page, text='Local model profiles', font=('Segoe UI', 15, 'bold')).pack(anchor='w')
    ttk.Label(page, text='Fast: 8B for short chats, 14B for coding. Balanced: 14B for chat and coding. Deep: 14B with extra reasoning for chat and the 30B coder for complex work; it can use system RAM and be slower on 12 GB VRAM.', wraplength=900).pack(anchor='w', pady=(8,12))
    row = ttk.Frame(page);row.pack(fill='x')
    app.model_profile = tk.StringVar(value=model_runtime.settings(root).get('profile','Balanced'))
    style=ttk.Style(app.root)
    style.map('NovaProfile.TCombobox',fieldbackground=[('readonly',ui_theme.CARD)],foreground=[('readonly',ui_theme.FG)],selectbackground=[('readonly',ui_theme.CARD)],selectforeground=[('readonly',ui_theme.FG)])
    chooser = ttk.Combobox(row, textvariable=app.model_profile, values=list(model_runtime.PROFILES), state='readonly', width=18,style='NovaProfile.TCombobox')
    chooser.pack(side='left')
    def select(event=None):
        model_runtime.configure(root,profile=app.model_profile.get())
        app.status.set('Model profile: '+app.model_profile.get()+'. Applies to the next model request.')
    chooser.bind('<<ComboboxSelected>>',select)
    ttk.Label(row,text='Environment model overrides take priority.').pack(side='left',padx=12)
    semantic = tk.BooleanVar(value=model_runtime.settings(root).get('semantic_search',False))
    ttk.Checkbutton(page,text='Hybrid manual search (meaning + exact words, computed locally)',variable=semantic,
                    command=lambda:model_runtime.configure(root,semantic_search=semantic.get())).pack(anchor='w',pady=(18,8))
    app.index_status = tk.StringVar(value=SemanticIndex(root).status())
    ttk.Label(page,textvariable=app.index_status,wraplength=900).pack(anchor='w')
    app.index_button = ttk.Button(page,text='Build / update manual index',command=lambda:start_index(app))
    app.index_button.pack(anchor='w',pady=8)
    ttk.Label(page,text='Requires the local Ollama model embeddinggemma. Only new or changed passages are indexed; original manuals and earlier versions are preserved. If semantic retrieval is unavailable, exact-word search continues.',wraplength=900).pack(anchor='w')
    ttk.Label(page,text='Recent local calls and checks',font=('Segoe UI',12,'bold')).pack(anchor='w',pady=(20,8))
    footer=ttk.Frame(page);footer.pack(side='bottom',fill='x',pady=(8,0))
    table=ttk.Treeview(page,columns=('purpose','model','seconds','input','output','status'),show='headings',height=6)
    for key,title,width in [('purpose','Task',75),('model','Model / engine',185),('seconds','Seconds',85),('input','Input tokens',100),('output','Output tokens',100),('status','Status',80)]:
        table.heading(key,text=title);table.column(key,width=width)
    table.pack(fill='both',expand=True)
    def refresh():
        table.delete(*table.get_children())
        for purpose,model,seconds,inp,out,status in model_runtime.runtime(root).recent():
            table.insert('','end',values=(purpose,model,f'{seconds:.2f}',inp,out,status))
    ttk.Button(footer,text='Refresh metrics',command=refresh).pack(anchor='w',pady=(0,8))
    ttk.Label(footer,text='Metrics contain timing, token counts and status only. A completed call does not prove the task or its answer is correct.',wraplength=900).pack(anchor='w')
    refresh()


def start_index(app):
    if getattr(app,'_indexing',False):
        app._index_again=True;return
    app._indexing=True
    app.index_button.configure(state='disabled')
    state={'text':'Preparing local manual index…','done':False,'error':None}
    def worker():
        try:
            app.manual_library.semantic.build(app.manual_library,lambda text:state.update(text=text))
            state['text']=app.manual_library.semantic.status()
        except Exception as exc:
            state['error']=type(exc).__name__
            state['text']='Index unavailable. Exact-word search remains active. Check that Ollama and embeddinggemma are installed.'
        finally:state['done']=True
    threading.Thread(target=worker,daemon=True).start()
    def poll():
        app.index_status.set(state['text'])
        if state['done']:
            app._indexing=False;app.index_button.configure(state='normal')
            if getattr(app,'_index_again',False) and not getattr(app,'closing',False):
                app._index_again=False;app.root.after_idle(lambda:start_index(app))
            return
        if not getattr(app,'closing',False):app.root.after(250,poll)
    poll()
