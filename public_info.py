# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local copyright, warranty and source/license notices for the desktop UI."""
from pathlib import Path
import tkinter as tk
from tkinter import ttk
import ui_theme


def show_about(parent):
    root=Path(__file__).resolve().parent
    window=tk.Toplevel(parent);window.title('Nova — About & license')
    window.geometry('760x540');window.configure(bg=ui_theme.BG)
    body=ttk.Frame(window,padding=16);body.pack(fill='both',expand=True)
    ttk.Label(body,text='Nova Local Assistant · alpha',font=('Segoe UI',16,'bold')).pack(anchor='w')
    ttk.Label(body,text='Copyright (C) 2026 Nova contributors\nAGPL-3.0-or-later · No warranty',padding=(0,10)).pack(anchor='w')
    box=ui_theme.text_widget(body,wrap='word',height=18)
    scroll=ttk.Scrollbar(body,command=box.yview);box.configure(yscrollcommand=scroll.set)
    scroll.pack(side='right',fill='y');box.pack(fill='both',expand=True)
    text='Source code is the Python source distributed alongside this window.\nYou may modify and redistribute it under the license terms.\n\n'
    for name in ('NOTICE','LICENSE','THIRD_PARTY_NOTICES.md'):
        path=root/name
        text+='\n'+name+'\n'+(path.read_text(encoding='utf-8') if path.is_file() else 'See the corresponding source distribution.')+'\n'
    box.insert('1.0',text);box.configure(state='disabled')
    return window
