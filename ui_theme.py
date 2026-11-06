# SPDX-License-Identifier: AGPL-3.0-or-later
"""Nova's dark workspace, native window branding, and shared UI surfaces."""
import tkinter as tk
from tkinter import ttk
from pathlib import Path
import sys

BG = "#101923"
SIDEBAR = "#0B121B"
CARD = "#172431"
SURFACE = "#13202C"
FG = "#E7EEF4"
MUTED = "#91A4B5"
LINE = "#263847"
ACCENT = "#45E8CE"
ACCENT_ACTIVE = "#72F3DD"
ACCENT_DISABLED = "#244E49"
DANGER = "#F17583"
DANGER_ACTIVE = "#FF97A2"
OK = ACCENT
SELECTED = "#203C48"
FONT = "Segoe UI"
ASSETS = Path(__file__).resolve().parent / "assets" / "icons"


def prepare_process():
    if sys.platform == 'win32':
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('Nova.LocalAssistant')
        except (OSError, AttributeError):
            pass


def brand_image(master, size=36):
    try:
        from PIL import Image, ImageTk
        with Image.open(ASSETS / 'nova-modern.png') as source:
            return ImageTk.PhotoImage(source.resize((size, size), Image.Resampling.LANCZOS), master=master)
    except (OSError, tk.TclError):
        return None


def _native_titlebar(window):
    if sys.platform != 'win32':return
    try:
        import ctypes
        user32=ctypes.windll.user32
        user32.GetAncestor.restype=ctypes.c_void_p
        hwnd=user32.GetAncestor(ctypes.c_void_p(window.winfo_id()),2)
        dark=ctypes.c_int(1)
        dwm=ctypes.windll.dwmapi.DwmSetWindowAttribute
        for attribute in (20,19):
            dwm(ctypes.c_void_p(hwnd),attribute,ctypes.byref(dark),ctypes.sizeof(dark))
        for attribute,color in ((35,SIDEBAR),(36,FG),(34,LINE)):
            rgb=tuple(int(color[i:i+2],16) for i in (1,3,5))
            value=ctypes.c_uint(rgb[0] | rgb[1]<<8 | rgb[2]<<16)
            dwm(ctypes.c_void_p(hwnd),attribute,ctypes.byref(value),ctypes.sizeof(value))
    except (OSError, AttributeError, tk.TclError):
        pass


def window_brand(window):
    """Use the desktop Nova asset for this window and future owned dialogs."""
    try:
        icon=ASSETS/'nova-modern.ico'
        if sys.platform=='win32' and icon.is_file():
            window.iconbitmap(default=str(icon))
            window.iconbitmap(str(icon))
        else:
            photo=brand_image(window,64)
            if photo is not None:
                window._nova_icon=photo
                window.iconphoto(True,photo)
    except tk.TclError:
        pass
    # Keep scheduled commands owned by the root, which also cancels pending
    # callbacks during shutdown; child-owned callbacks can otherwise be deleted
    # twice when an unopened settings window is destroyed.
    window._root().after_idle(lambda:_native_titlebar(window))
    window.bind('<Map>',lambda event:_native_titlebar(window) if event.widget is window else None,add='+')


def hover(widget, normal, active):
    widget.bind('<Enter>',lambda e:widget.configure(bg=active),add='+')
    widget.bind('<Leave>',lambda e:widget.configure(bg=normal),add='+')


class Surface(tk.Frame):
    """A rounded surface with a normal frame inside for accessible Tk controls."""
    def __init__(self,parent,fill=CARD,border=LINE,radius=18,padding=12,**kwargs):
        super().__init__(parent,bg=parent.cget('bg'),**kwargs)
        self.fill,self.border,self.radius,self.padding=fill,border,radius,padding
        self.canvas=tk.Canvas(self,bg=self.cget('bg'),highlightthickness=0,bd=0,height=70)
        self.canvas.pack(fill='both',expand=True)
        self.body=tk.Frame(self.canvas,bg=fill)
        self._window=self.canvas.create_window(padding,padding,anchor='nw',window=self.body)
        self.canvas.bind('<Configure>',self._resize)
        self.body.bind('<Configure>',self._height)

    def _height(self,event=None):
        height=self.body.winfo_reqheight()+2*self.padding
        if int(self.canvas.cget('height'))!=height:self.canvas.configure(height=height)

    def _resize(self,event):
        w,h=event.width,event.height;r=min(self.radius,w//2,h//2)
        self.canvas.delete('surface')
        points=[r,1,w-r,1,w-1,1,w-1,r,w-1,h-r,w-1,h-1,w-r,h-1,r,h-1,1,h-1,1,h-r,1,r,1,1]
        self.canvas.create_polygon(points,smooth=True,splinesteps=24,fill=self.fill,outline=self.border,tags='surface')
        self.canvas.tag_lower('surface')
        self.canvas.itemconfigure(self._window,width=max(1,w-2*self.padding))


def apply(root):
    root.configure(bg=BG)
    root.option_add('*Font',(FONT,10))
    root.option_add('*Menu.background',CARD)
    root.option_add('*Menu.foreground',FG)
    root.option_add('*Menu.activeBackground',SELECTED)
    root.option_add('*Menu.activeForeground',FG)
    root.option_add('*Menu.borderWidth',0)
    window_brand(root)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(".", background=BG, foreground=FG, font=("Segoe UI", 10))
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Title.TLabel", background=BG, foreground=FG, font=("Segoe UI", 18, "bold"))
    style.configure("Subtitle.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
    style.configure("Status.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 9))
    style.configure("State.TLabel", background=BG, foreground=MUTED, font=("Segoe UI", 12, "bold"))
    style.configure("TLabelframe", background=BG)
    style.configure("TLabelframe.Label", background=BG, foreground=FG, font=("Segoe UI", 10, "bold"))
    style.configure("TButton",padding=(13,8),background=CARD,foreground=FG,borderwidth=0,relief='flat',focusthickness=0)
    style.map("TButton",background=[('disabled',SURFACE),('active',SELECTED)],foreground=[('disabled','#566A7B')])
    style.configure("Quiet.TButton",padding=(9,5),background=BG,font=(FONT,9))
    style.map("Quiet.TButton",background=[('active',CARD)])
    style.configure("Toolbar.TButton",padding=(9,5),background=CARD,font=(FONT,9))
    style.configure("Accent.TButton", background=ACCENT, foreground=SIDEBAR, padding=(14,9),font=(FONT,10,'bold'))
    style.map("Accent.TButton",
              background=[("active", ACCENT_ACTIVE), ("disabled", ACCENT_DISABLED)],
              foreground=[("disabled", MUTED)])
    style.configure("Danger.TButton", background="#402731", foreground="#FFD7DD", padding=(12,6))
    style.map("Danger.TButton",
              background=[("active", "#633343"), ("disabled", SURFACE)],
              foreground=[("disabled", "#566A7B")])
    style.configure("TNotebook", background=BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(14, 7), font=("Segoe UI", 10), background=BG, foreground=MUTED)
    style.map("TNotebook.Tab",
              foreground=[("selected", FG)],
              background=[("selected", CARD)])
    style.configure("TCombobox", padding=3, fieldbackground=CARD, foreground=FG, background=CARD)
    style.configure("TCheckbutton", background=BG, foreground=FG)
    style.configure("TEntry", padding=3, fieldbackground=CARD, foreground=FG)
    style.configure('Vertical.TScrollbar',background=LINE,troughcolor=BG,bordercolor=BG,arrowcolor=MUTED,
                    lightcolor=LINE,darkcolor=LINE,width=8,borderwidth=0,arrowsize=0)
    style.map('Vertical.TScrollbar',background=[('active',MUTED)])
    style.configure('Treeview',background=CARD,fieldbackground=CARD,foreground=FG,rowheight=32,borderwidth=0)
    style.configure('Treeview.Heading',background=SURFACE,foreground=MUTED,relief='flat',padding=8)
    style.map('Treeview',background=[('selected',SELECTED)],foreground=[('selected',FG)])
    style.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=LINE, borderwidth=0)
    return style


def text_widget(parent, **kwargs):
    """A tk.Text that matches the theme (ttk has no Text)."""
    kwargs.setdefault("bg", CARD)
    kwargs.setdefault("fg", FG)
    kwargs.setdefault("relief", "flat")
    kwargs.setdefault("highlightthickness", 1)
    kwargs.setdefault("highlightbackground", LINE)
    kwargs.setdefault("highlightcolor", ACCENT)
    kwargs.setdefault("insertbackground", FG)
    kwargs.setdefault("font", ("Segoe UI", 10))
    return tk.Text(parent, **kwargs)


def listbox_widget(parent, **kwargs):
    kwargs.setdefault("bg", CARD)
    kwargs.setdefault("fg", FG)
    kwargs.setdefault("relief", "flat")
    kwargs.setdefault("highlightthickness", 1)
    kwargs.setdefault("highlightbackground", LINE)
    kwargs.setdefault("highlightcolor", ACCENT)
    kwargs.setdefault("font", ("Segoe UI", 10))
    return tk.Listbox(parent, **kwargs)
