# SPDX-License-Identifier: AGPL-3.0-or-later
"""Tk controls for the persistent manual library."""
import os
import tkinter as tk
from tkinter import ttk,filedialog,simpledialog
from manual_library import ManualLibrary
import ui_theme

class ManualMixin:
    def manual_init(self,wrap,pill,tabs,side):
        self.manual_library=ManualLibrary(self.core.ROOT);self.importing_manual=False
        self.manual_scope_value='off'
        footer=getattr(self,'sidebar_footer',side)
        existing=footer.pack_slaves()
        knowledge=tk.Frame(footer,bg=ui_theme.SIDEBAR)
        knowledge.pack(fill='x',pady=(0,12),**({'before':existing[0]} if existing and hasattr(self,'sidebar_footer') else {}))
        tk.Frame(knowledge,bg=ui_theme.LINE,height=1).pack(fill='x',pady=(0,12))
        tk.Label(knowledge,text='KNOWLEDGE',bg=ui_theme.SIDEBAR,fg=ui_theme.MUTED,font=('Segoe UI',8,'bold'),anchor='w').pack(fill='x',padx=8)
        self.manual_count=tk.StringVar(value='Saved manuals')
        link=tk.Label(knowledge,textvariable=self.manual_count,bg=ui_theme.SIDEBAR,fg=ui_theme.FG,font=('Segoe UI',9),anchor='w',padx=8,pady=8,cursor='hand2')
        link.pack(fill='x');ui_theme.hover(link,ui_theme.SIDEBAR,ui_theme.CARD)
        link.bind('<Button-1>',lambda e:(self.open_settings(),self.tabs.select(self.manual_page)))
        row=tk.Frame(knowledge,bg=ui_theme.SIDEBAR);row.pack(fill='x',pady=(0,4))
        self.import_manual_button=ttk.Button(row,text='+ Manual',style='Toolbar.TButton',command=self.import_manual)
        self.import_manual_button.pack(side='left')
        self.import_website_button=ttk.Button(row,text='+ Website',style='Toolbar.TButton',command=self.import_website)
        self.import_website_button.pack(side='left',padx=(8,0))
        self.manual_page=ttk.Frame(tabs,padding=12);tabs.add(self.manual_page,text='Manuals')
        ttk.Label(self.manual_page,text='Saved locally across restarts. New imports become current; older manuals remain available.',wraplength=900).pack(anchor='w')
        self.website_links=tk.BooleanVar(value=True)
        ttk.Checkbutton(self.manual_page,text='Website imports: include linked sections in the same documentation directory',variable=self.website_links).pack(anchor='w',pady=4)
        listing=ttk.Frame(self.manual_page);listing.pack(fill='both',expand=True,pady=8)
        self.manual_tree=ttk.Treeview(listing,columns=('title','version','pages','date'),show='headings',height=12)
        for col,title,width in [('title','Manual',400),('version','Version',60),('pages','Pages',70),('date','Imported',110)]:
            self.manual_tree.heading(col,text=title);self.manual_tree.column(col,width=width)
        scroll=ttk.Scrollbar(listing,command=self.manual_tree.yview)
        self.manual_tree.configure(yscrollcommand=scroll.set)
        scroll.pack(side='right',fill='y');self.manual_tree.pack(side='left',fill='both',expand=True)
        self.manual_tree.bind('<<TreeviewSelect>>',self.show_manual_details)
        self.manual_details=tk.StringVar()
        ttk.Label(self.manual_page,textvariable=self.manual_details,wraplength=900).pack(fill='x',pady=6)
        buttons=ttk.Frame(self.manual_page);buttons.pack(fill='x')
        for label,command in [('Import manual',self.import_manual),('Import website',self.import_website),
                              ('Open reference PDF',lambda:self.open_manual_file(True)),
                              ('Open saved original',lambda:self.open_manual_file(False)),('Refresh',self.refresh_manual_library)]:
            ttk.Button(buttons,text=label,command=command).pack(side='left',padx=(0,6))
        self.refresh_manual_library()
    def refresh_manual_library(self):
        self.manual_rows=self.manual_library.list()
        if hasattr(self,'manual_count'):self.manual_count.set(f'Saved manuals  ·  {len(self.manual_rows)}')
        selection=self.manual_tree.selection()
        self.manual_tree.delete(*self.manual_tree.get_children())
        for manual in self.manual_rows:
            self.manual_tree.insert('', 'end',iid=str(manual['id']),values=(manual['title'],manual['version'],manual['pages'],manual['imported'][:10]))
        if selection and self.manual_tree.exists(selection[0]):self.manual_tree.selection_set(selection[0])
    def selected_manual(self):
        selected=self.manual_tree.selection()
        return self.manual_library.get(selected[0]) if selected else None
    def show_manual_details(self,event=None):
        manual=self.selected_manual()
        self.manual_details.set((f"[M{manual['id']}] {manual['name']} · {manual['notes']}" if manual else 'Select a manual.'))
    def open_manual_file(self,reference=True):
        manual=self.selected_manual()
        if not manual:self.status.set('Select a saved manual first.');return
        folder=self.manual_library.folder/manual['folder']
        path=folder/'reference.pdf' if reference else next(folder.glob('source.*'),None)
        try:
            if path is None:raise ValueError('Saved original not found.')
            os.startfile(str(path))
        except Exception as e:self.status.set('Could not open manual: '+str(e))
    def import_manual(self):
        if self.busy or self.recorder:
            self.status.set('Finish the current recording or reply before importing a manual.');return
        self._wake_stop()
        path=filedialog.askopenfilename(parent=self.root,title='Import manual',filetypes=[('Manuals','*.pdf *.docx *.txt *.md'),('PDF','*.pdf'),('Word document','*.docx'),('Text','*.txt *.md')])
        if path:self.import_manual_path(path)
    def import_manual_path(self,path):
        self.start_manual_import(path,False)
    def import_website(self):
        if self.busy or self.recorder:
            self.status.set('Finish the current recording or reply before importing a website.');return
        self._wake_stop()
        url=simpledialog.askstring('Import website manual','Public documentation URL (text and article images will be saved):',parent=self.root)
        if url:self.import_website_url(url)
    def import_website_url(self,url):
        self.start_manual_import(url,True)
    def start_manual_import(self,path,website):
        if self.busy or self.recorder:return
        self._wake_stop();self.importing_manual=True
        def progress(text):self.voice_events.put(('manual_progress',text))
        def ocr(encoded):
            from manual_ocr import transcribe
            return transcribe(encoded)
        def done(manual):
            self.importing_manual=False;self.context_picture=None;self.chat_history=[]
            self._manual_followup_ids=[manual['id']]
            if self.attached_picture:self.remove_picture()
            self.refresh_manual_library();self.manual_tree.selection_set(str(manual['id']));self.show_manual_details()
            action='Already saved; selected' if manual['duplicate'] else 'Saved and selected'
            text=f"{action} manual [M{manual['id']}]: {manual['name']} (v{manual['version']}, {manual['pages']} pages). Ask a question to use it. Older manuals remain in the library.\n{manual['notes']}"
            self.chat_append('Assistant',text);self.status.set(text.split('\n')[0])
            from model_runtime import settings
            if settings(self.core.ROOT).get('semantic_search',False):
                from performance_ui import start_index
                start_index(self)
        def failed(error):
            self.importing_manual=False;self.status.set('Could not import manual: '+error)
        include_links=self.website_links.get() if website else False
        def vision(encoded,prompt):return self.core.vision_reply(encoded,prompt,tokens=500)
        self.work(lambda:(self.manual_library.import_website(path,include_links,progress,ocr,vision) if website
                          else self.manual_library.import_file(path,progress,ocr)),done,failed)
