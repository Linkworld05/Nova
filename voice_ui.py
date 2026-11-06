# SPDX-License-Identifier: AGPL-3.0-or-later
"""Desktop voice controls; all Tk mutations stay on the UI thread."""
import difflib, json, queue, re, threading, time, uuid, wave
from pathlib import Path
from datetime import datetime, timezone
import tkinter as tk
from tkinter import ttk, messagebox
import sounddevice as sd
import ui_theme
from local_audio import Recorder, Transcriber, Speaker, microphones, voices, timestamp, apply_pending_voice
from computer_audio import ComputerRecorder, loopback_outputs, DEFAULT_OUTPUT

def plain_speech(text):
    """Strip Markdown so spoken replies don't read out formatting symbols."""
    text = re.sub(r"```.*?```", " code ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"\*\*([^*]*)\*\*", r"\1", text)
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.M)
    text = re.sub(r"^\s*[-*+]\s+", "", text, flags=re.M)
    text = re.sub(r"^\s*>\s?", "", text, flags=re.M)
    return text.strip()

class WakeRecorder(Recorder):
    """Recorder that ends a chunk after ~1s of silence, so short wake phrases are rarely split across chunks.
    More sensitive voice-activity threshold than conversation recording (wake words are often spoken softly)."""
    vad_threshold=.004
    quiet_delay=1.2
    def discard_chunk(self,path):
        """Remove processed audio, retaining the folder while its writer is alive."""
        p=Path(path)
        try:
            p.unlink(missing_ok=True);p.with_suffix(".json").unlink(missing_ok=True)
        except OSError:pass
        if not (self.writer and self.writer.is_alive()):self._remove_empty_folder()
    def _remove_empty_folder(self):
        try:self.folder.rmdir()
        except OSError:pass  # Queued chunks may still be awaiting transcription.
    def stop(self):
        files=super().stop()
        self._remove_empty_folder()
        return files
    def _write(self):
        pieces=[];frames=0;start=0.;end=0.;index=0
        def flush():
            nonlocal pieces,frames,index
            if not frames:return
            index+=1;path=self.folder/f"chunk-{index:05d}.wav"
            temp=path.with_suffix(".part")
            with wave.open(str(temp),"wb") as w:
                w.setnchannels(1);w.setsampwidth(2);w.setframerate(self.rate);w.writeframes(b"".join(pieces))
            temp.replace(path)
            path.with_suffix(".json").write_text(json.dumps({"start":start,"end":end}),encoding="utf-8")
            self.files.append(path)
            self.on_chunk(path,start,end)
            pieces=[];frames=0
        try:
            while True:
                item=self.blocks.get()
                if item is None:break
                raw,end=item
                if not frames:start=max(0.,end-len(raw)/(2*self.rate))
                pieces.append(raw);frames+=len(raw)//2
                quiet=self.last_voice is not None and time.monotonic()-self.last_voice>=getattr(self,"quiet_delay",.9)
                if frames>=self.rate*self.chunk_seconds or (frames>=int(self.rate*1.2) and quiet):flush()
            flush()
        except Exception as e:self.error=str(e);self.on_error(f"Recording save failed: {e}")

class VoiceMixin:
    def voice_init(self,frame,tabs,core):
        self.core=core;self.voice_events=queue.Queue();self.audio_jobs=queue.Queue()
        self.recorder=None;self.record_kind=None;self.session_id=None;self.finishing=set()
        self.closing=False;self.handsfree=False;self.next_listen=0.;self.question_generation=0
        self.was_speaking=False;self.last_response=""
        self.wake_recorder=None;self.wake_jobs=queue.Queue()
        try:self.chat_history=core.load_turns(6,thread_id=self.active_chat_id)
        except Exception:self.chat_history=[]
        self.transcriber=Transcriber(core.ROOT/"models")
        self.speaker=Speaker(lambda error:self.voice_events.put(("error","Voice playback: "+error)))
        self.config_path=core.ROOT/"voice-settings.json"
        try:self.settings=json.loads(self.config_path.read_text(encoding="utf-8"))
        except (OSError,ValueError):self.settings={}
        self.settings=apply_pending_voice(self.config_path,self.settings)
        self._audio_schema()
        page=ttk.Frame(tabs,padding=10);tabs.insert(0,page,text="Record & talk");tabs.select(page)
        devices=ttk.LabelFrame(page,text="Audio settings",padding=8);devices.pack(fill="x")
        ttk.Label(devices,text="Microphone").grid(row=1,column=0,sticky="w")
        self.mic=ttk.Combobox(devices,state="readonly",width=69);self.mic.grid(row=1,column=1,columnspan=3,sticky="ew",padx=6)
        ttk.Button(devices,text="Refresh inputs",command=self.load_inputs).grid(row=1,column=4,padx=4)
        ttk.Label(devices,text="Speech language").grid(row=2,column=0,sticky="w",pady=8)
        self.language=ttk.Combobox(devices,state="readonly",values=["Automatic","English","Spanish"],width=14)
        self.language.set(self.settings.get("language","Automatic"));self.language.grid(row=2,column=1,sticky="w",padx=6)
        ttk.Label(devices,text="Reply voice").grid(row=2,column=2,sticky="e")
        self.reply_voice=ttk.Combobox(devices,state="readonly",width=29);self.reply_voice.grid(row=2,column=3,sticky="ew",padx=6)
        self.speak_enabled=tk.BooleanVar(value=self.settings.get("speak",False))
        ttk.Checkbutton(devices,text="Speak replies",variable=self.speak_enabled,command=self.voice_preferences).grid(row=2,column=4,sticky="w")
        self.pc_control=tk.BooleanVar(value=bool(self.settings.get("pc_control",False)))
        ttk.Checkbutton(devices,text="PC control (open apps, media, volume, lock)",variable=self.pc_control,command=self.voice_preferences).grid(row=3,column=1,columnspan=4,sticky="w",pady=(6,0))
        ttk.Label(devices,text="Wake word").grid(row=4,column=0,sticky="w",pady=(8,0))
        self.wake_enabled=tk.BooleanVar(value=bool(self.settings.get("wake",False)))
        ttk.Checkbutton(devices,text="Always listen for",variable=self.wake_enabled,command=self._wake_toggled).grid(row=4,column=1,sticky="w",pady=(8,0))
        self.wake_word=tk.StringVar(value=self.settings.get("wake_word","nova"))
        ttk.Entry(devices,textvariable=self.wake_word,width=12).grid(row=4,column=1,sticky="w",padx=(118,0),pady=(8,0))
        ttk.Label(devices,text="say the word, then your command (keeps the microphone busy)").grid(row=4,column=2,columnspan=3,sticky="w",pady=(8,0))
        ttk.Label(devices,text="Conversation source").grid(row=0,column=0,sticky="w")
        self.capture_source=ttk.Combobox(devices,state="readonly",values=["Microphone","Computer audio","Both"],width=17)
        source=self.settings.get("capture_source","Both")
        self.capture_source.set(source if source in ("Microphone","Computer audio","Both") else "Both")
        self.capture_source.grid(row=0,column=1,sticky="w",padx=6,pady=(0,8))
        ttk.Label(devices,text="PC playback output").grid(row=0,column=2,sticky="e")
        self.pc_output=ttk.Combobox(devices,state="readonly",width=43)
        self.pc_output.grid(row=0,column=3,columnspan=2,sticky="ew",padx=6,pady=(0,8))
        devices.columnconfigure(3,weight=1)
        for widget in (self.mic,self.language,self.reply_voice,self.capture_source,self.pc_output):widget.bind("<<ComboboxSelected>>",lambda e:self.voice_preferences())
        self.mic_state=tk.StringVar(value="Microphone OFF")
        self.mic_label=ttk.Label(page,textvariable=self.mic_state,style="State.TLabel",foreground=ui_theme.MUTED)
        self.mic_label.pack(anchor="w",pady=(10,3))
        self.meter=ttk.Progressbar(page,maximum=100);self.meter.pack(fill="x",pady=(0,8))
        buttons=ttk.Frame(page);buttons.pack(fill="x")
        self.record_button=ttk.Button(buttons,text="Record conversation",command=self.start_conversation);self.record_button.pack(side="left")
        self.stop_button=ttk.Button(buttons,text="Stop & summarize",command=self.stop_recording);self.stop_button.pack(side="left",padx=6)
        self.ask_voice_button=ttk.Button(buttons,text="Ask by voice",command=self.start_question);self.ask_voice_button.pack(side="left")
        self.chat_button=ttk.Button(buttons,text="Start voice chat",command=self.toggle_voice_chat);self.chat_button.pack(side="left",padx=6)
        ttk.Button(buttons,text="Stop speaking",command=self.stop_speaking).pack(side="left")
        ttk.Label(page,text="Recording saves local audio and transcribes about every 15 seconds. Voice questions stop after a short pause.").pack(anchor="w",pady=(8,2))
        ttk.Label(page,text="Voice questions use the microphone. Conversation source applies to recording. Capture stays off during replies.").pack(anchor="w",pady=(0,6))
        ttk.Label(page,text="Live conversation transcript").pack(anchor="w")
        self.live_transcript=ui_theme.text_widget(page,height=6,wrap="word",state="disabled");self.live_transcript.pack(fill="both",expand=True,pady=(3,6))
        self.heard=tk.StringVar(value="Ask a question by voice, or use the Ask memory tab.")
        ttk.Label(page,textvariable=self.heard,wraplength=960).pack(anchor="w")
        self.voice_reply=ui_theme.text_widget(page,height=6,wrap="word",state="disabled");self.voice_reply.pack(fill="both",expand=True,pady=6)
        bottom=ttk.Frame(page);bottom.pack(fill="x")
        ttk.Button(bottom,text="Read last reply",command=lambda:self.speak(self.last_response)).pack(side="left")
        ttk.Button(bottom,text="Retry unfinished transcription / summaries",command=lambda:self.queue_pending(True)).pack(side="left",padx=8)
        self.audio_state=tk.StringVar(value="Local Whisper Small · Audio and transcripts remain on this PC")
        ttk.Label(bottom,textvariable=self.audio_state).pack(side="left",padx=6)
        self.load_inputs()
        threading.Thread(target=self._load_voices,daemon=True).start()
        threading.Thread(target=self._transcription_worker,daemon=True).start()
        threading.Thread(target=self._wake_worker,daemon=True).start()
        self.queue_pending(False)
        self.root.protocol("WM_DELETE_WINDOW",self.voice_close)

    def _audio_schema(self):
        with self.core.connect() as db:
            db.execute("CREATE TABLE IF NOT EXISTS voice_sessions(sid INTEGER PRIMARY KEY, folder TEXT NOT NULL, language TEXT, state TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS audio_chunks(path TEXT PRIMARY KEY,sid INTEGER NOT NULL,start REAL NOT NULL,end REAL NOT NULL,transcript TEXT NOT NULL DEFAULT '',status TEXT NOT NULL,error TEXT NOT NULL DEFAULT '')")
            db.execute("UPDATE audio_chunks SET status='pending' WHERE status IN ('working','queued')")
            db.execute("UPDATE voice_sessions SET state='interrupted' WHERE state='recording'")
            sessions=db.execute("SELECT sid,folder FROM voice_sessions").fetchall()
        # A finished WAV can survive a crash before its database registration.
        for sid,folder in sessions:
            elapsed=0.
            for path in sorted(Path(folder).glob("chunk-*.wav")):
                with wave.open(str(path),"rb") as w:duration=w.getnframes()/w.getframerate()
                try:meta=json.loads(path.with_suffix(".json").read_text());start=meta["start"];end=meta["end"]
                except (OSError,ValueError,KeyError):start=elapsed;end=start+duration
                elapsed=end
                with self.core.connect() as db:
                    db.execute("INSERT OR IGNORE INTO audio_chunks(path,sid,start,end,status) VALUES(?,?,?,?, 'pending')",(str(path),sid,start,end))
        with self.core.connect() as db:
            self.finishing.update(r[0] for r in db.execute("SELECT sid FROM voice_sessions WHERE state IN ('interrupted','finishing','summarizing')"))

    def load_inputs(self):
        if self.recorder:return
        try:
            output=self.pc_output.get() or self.settings.get("pc_output",DEFAULT_OUTPUT)
            self.pc_output["values"]=[DEFAULT_OUTPUT]+[name for _,name in loopback_outputs()]
            self.pc_output.set(output if output in self.pc_output["values"] else DEFAULT_OUTPUT)
            self.inputs=microphones()
            self.mic["values"]=[label for _,label in self.inputs]
            saved=self.settings.get("microphone")
            chosen=next((i for i,(_,label) in enumerate(self.inputs) if label==saved),None)
            if chosen is None:chosen=next((i for i,(idx,_) in enumerate(self.inputs) if idx==sd.default.device[0]),0)
            if self.inputs:self.mic.current(chosen)
            else:self.status.set("No microphone found. Connect one, then Refresh inputs.")
        except Exception as e:self.status.set("Could not enumerate microphones: "+str(e))
    def _load_voices(self):
        try:self.voice_events.put(("voices",voices()))
        except Exception as e:self.voice_events.put(("error","Could not load Windows voices: "+str(e)))
    def voice_preferences(self):
        if not self.speak_enabled.get():self.speaker.stop()
        settings={"microphone":self.mic.get(),"language":self.language.get(),
            "voice":self.reply_voice.get(),"speak":self.speak_enabled.get(),
            "capture_source":self.capture_source.get(),"pc_output":self.pc_output.get(),
            "pc_control":self.pc_control.get(),
            "wake":self.wake_enabled.get(),"wake_word":self.wake_word.get().strip() or "nova"}
        temp=self.config_path.with_suffix(".tmp")
        temp.write_text(json.dumps(settings,indent=2),encoding="utf-8");temp.replace(self.config_path)
        self.settings=settings
    def selected_device(self):
        idx=self.mic.current()
        if idx<0:raise RuntimeError("Select a microphone first.")
        return self.inputs[idx][0]
    def selected_language(self):return {"English":"en","Spanish":"es"}.get(self.language.get())
    def _can_start(self):
        if self.recorder or self.busy or self.audio_jobs.unfinished_tasks or self.closing or self.finishing:
            self.status.set("Finish the current recording or request before starting another.")
            return False
        return True
    def start_conversation(self):
        if not self._can_start():return
        self._wake_stop()
        self.handsfree=False;self.speaker.stop()
        try:
            mode=self.capture_source.get()
            device=self.selected_device() if mode!="Computer audio" else None
            language=self.selected_language()
            folder=self.core.ROOT/"recordings"/(datetime.now().strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8])
            with self.core.connect() as db:
                sid=db.execute("INSERT INTO conversations(created,title,transcript) VALUES(?,?,?)",
                    (datetime.now(timezone.utc).isoformat(),f"Recorded conversation ({mode}) "+datetime.now().strftime("%Y-%m-%d %H:%M"),"")).lastrowid
                db.execute("INSERT INTO voice_sessions VALUES(?,?,?,?)",(sid,str(folder),language,"recording"))
            def chunk(path,start,end):
                with self.core.connect() as db:
                    db.execute("INSERT OR IGNORE INTO audio_chunks(path,sid,start,end,status) VALUES(?,?,?,?, 'queued')",(str(path),sid,start,end))
                self.audio_jobs.put(("conversation",sid,str(path),start,language,0))
            error_handler=lambda error:self.voice_events.put(("capture_error",error))
            if mode=="Microphone":
                rec=Recorder(folder,device,chunk,error_handler)
            else:
                rec=ComputerRecorder(folder,device,chunk,error_handler,mode=mode,output_name=self.pc_output.get())
            folder.mkdir(parents=True,exist_ok=True)
            (folder/"capture-settings.json").write_text(json.dumps({"source":mode,"microphone":self.mic.get() if mode!="Computer audio" else None,"playback_output":self.pc_output.get() if mode!="Microphone" else None},indent=2),encoding="utf-8")
            rec.start();self.recorder=rec;self.record_kind="conversation";self.session_id=sid;self.active_source=mode
            self.display(self.live_transcript,"");self.status.set(f"Recording conversation [{sid}]. Transcription begins after the first chunk.")
            self.refresh()
        except Exception as e:
            if 'sid' in locals():
                with self.core.connect() as db:db.execute("UPDATE voice_sessions SET state='error' WHERE sid=?",(sid,))
            self.status.set("Recording could not start: "+str(e))

    def start_question(self):
        if not self._can_start():return
        self._wake_stop()
        self.speaker.stop();self.question_generation+=1
        token=self.question_generation
        try:
            device=self.selected_device();language=self.selected_language()
            folder=self.core.ROOT/"voice-questions"/uuid.uuid4().hex
            def chunk(path,start,end):
                self.audio_jobs.put(("question",None,str(path),start,language,token))
            rec=Recorder(folder,device,chunk,lambda error:self.voice_events.put(("capture_error",error)),chunk_seconds=35)
            rec.start();self.recorder=rec;self.record_kind="question"
            self.heard.set("Listening for your question… Pause for 2 seconds when finished.")
            self.status.set("Listening. Stop & send also ends the question manually.")
        except Exception as e:
            self.handsfree=False;self.status.set("Microphone: "+str(e))

    def stop_recording(self):
        rec=self.recorder
        if not rec:return
        kind=self.record_kind;sid=self.session_id
        try:
            rec.stop();self.recorder=None;self.record_kind=None
            if kind=="conversation":
                with self.core.connect() as db:db.execute("UPDATE voice_sessions SET state='finishing' WHERE sid=?",(sid,))
                self.finishing.add(sid)
                self.status.set("Capture off. Finishing transcription, then creating a summary.")
            else:self.status.set("Microphone off. Transcribing your question…")
            if rec.error:self.status.set("Microphone off. "+rec.error)
        except Exception as e:
            self.handsfree=False;self.status.set("Could not finish recording: "+str(e))
    def toggle_voice_chat(self):
        self._wake_stop()
        if self.handsfree:
            self.handsfree=False;self.question_generation+=1;self.speaker.stop()
            if self.record_kind=="question":self.stop_recording()
            self.heard.set("Voice chat stopped. Click Ask by voice or Start voice chat when ready.")
            self.status.set("Voice chat stopped. Microphone off.")
        elif self._can_start():
            self.handsfree=True;self.next_listen=time.monotonic()+.4;self.speaker.stop()
    def stop_speaking(self):
        self.speaker.stop()
        if self.handsfree:self.next_listen=time.monotonic()+.8
    # --- wake word (always listening) ---
    def _wake_word(self):
        return (self.wake_word.get() or "nova").strip().lower() or "nova"
    def _wake_toggled(self):
        self.voice_preferences()
        if not self.wake_enabled.get():self._wake_stop()
    def _wake_start(self):
        if self.wake_recorder is not None or self.closing:return
        try:
            device=self.selected_device();language=self.selected_language()
            folder=self.core.ROOT/"wake-listen"/uuid.uuid4().hex
            # Carry the owner with each chunk: older jobs can outlive a wake session.
            def chunk(path,start,end):self.wake_jobs.put((rec,str(path)))
            rec=WakeRecorder(folder,device,chunk,lambda error:self.voice_events.put(("wake_error",error)),chunk_seconds=12)
            rec.start();self.wake_recorder=rec
            self.status.set(f"Wake word active — say '{self._wake_word()}', then your command.")
        except Exception as e:
            self.wake_recorder=None;self.status.set("Wake word could not start: "+str(e))
    def _wake_stop(self):
        rec=self.wake_recorder;self.wake_recorder=None
        if rec:
            try:rec.stop()
            except Exception:pass
    def _wake_match(self,text):
        """Find the wake word in text; exact first, then tolerant of ASR misspellings like 'Nowa'/'Noba'."""
        word=self._wake_word()
        m=re.search(r"\b"+re.escape(word)+r"\b",text,re.IGNORECASE)
        if m:return m
        for tok in re.findall(r"[a-zà-ÿ']+",text.lower()):
            if not (3<=len(tok)<=len(word)+1):continue
            if tok[0]!=word[0] or tok[-1]!=word[-1]:continue
            if difflib.SequenceMatcher(None,tok,word).ratio()>=.70:
                m=re.search(re.escape(tok),text,re.IGNORECASE)
                if m:return m
        return None
    def _wake_confirm(self):
        def play():
            try:
                import winsound
                try:winsound.Beep(880,110);winsound.Beep(1320,150)
                except RuntimeError:winsound.PlaySound("SystemAsterisk",winsound.SND_ALIAS)
            except Exception:pass
        threading.Thread(target=play,daemon=True).start()
    def _wake_worker(self):
        while True:
            rec,path=self.wake_jobs.get()
            try:
                if self.wake_recorder is not rec or self.closing or self.speaker.active.is_set():continue
                try:segments,detected=self.transcriber.transcribe(path,self.selected_language())
                except Exception:continue
                text=" ".join(s["text"] for s in segments).strip()
                if text:self.voice_events.put(("wake",text))
            finally:
                rec.discard_chunk(path);self.wake_jobs.task_done()
    def speak(self,text):
        if self.recorder:return
        if text:self.speaker.say(text,self.reply_voice.get())
    def voice_answer(self,text):
        text=self.core.visible_answer(text)
        self.last_response=text;self.display(self.voice_reply,text)
        if self.speak_enabled.get() and not self.closing:self.speak(plain_speech(text.split("\n\nRetrieved sources:")[0].split("\n\nManual sources:")[0]))
        self.next_listen=time.monotonic()+.8

    def queue_pending(self,include_errors=False):
        if self.recorder:return
        statuses=("pending","error") if include_errors else ("pending",)
        with self.core.connect() as db:
            rows=db.execute("SELECT a.path,a.sid,a.start,v.language FROM audio_chunks a JOIN voice_sessions v ON v.sid=a.sid WHERE a.status IN ("+",".join("?" for _ in statuses)+") ORDER BY a.sid,a.start",statuses).fetchall()
            for path,sid,start,language in rows:
                db.execute("UPDATE audio_chunks SET status='queued',error='' WHERE path=?",(path,))
                self.audio_jobs.put(("conversation",sid,path,start,language,0));self.finishing.add(sid)
            if include_errors:
                self.finishing.update(r[0] for r in db.execute("SELECT sid FROM voice_sessions WHERE state='summary_error'"))
        if rows:self.status.set(f"Resuming {len(rows)} saved audio chunks.")
    def _transcription_worker(self):
        while True:
            kind,sid,path,start,language,token=self.audio_jobs.get()
            try:
                if kind=="conversation":
                    with self.core.connect() as db:db.execute("UPDATE audio_chunks SET status='working' WHERE path=?",(path,))
                segments,detected=self.transcriber.transcribe(path,language)
                if kind=="conversation":
                    text="\n".join(f"[{timestamp(start+s['start'])}–{timestamp(start+s['end'])}] {s['text']}" for s in segments)
                    with self.core.connect() as db:
                        db.execute("UPDATE audio_chunks SET transcript=?,status='done',error='' WHERE path=?",(text,path))
                        transcript="\n".join(r[0] for r in db.execute("SELECT transcript FROM audio_chunks WHERE sid=? AND status='done' AND transcript!='' ORDER BY start,path",(sid,)))
                        db.execute("UPDATE conversations SET transcript=?,summary='' WHERE id=?",(transcript,sid))
                    self.voice_events.put(("transcribed",sid))
                else:
                    text=" ".join(s["text"] for s in segments)
                    self.voice_events.put(("question",token,text))
            except Exception as e:
                if kind=="conversation":
                    with self.core.connect() as db:db.execute("UPDATE audio_chunks SET status='error',error=? WHERE path=?",(str(e),path))
                    self.voice_events.put(("asr_error",sid,str(e)))
                else:self.voice_events.put(("question_error",token,str(e)))
            finally:
                if kind=="question":
                    # Voice questions are temporary; conversation recordings are retained.
                    try:
                        p=Path(path);p.unlink(missing_ok=True);p.with_suffix(".json").unlink(missing_ok=True);p.parent.rmdir()
                    except OSError:pass
                self.audio_jobs.task_done()

    def _finish_sessions(self):
        if self.busy or self.recorder or not self.finishing:return
        sid=min(self.finishing)
        with self.core.connect() as db:
            pending=db.execute("SELECT COUNT(*) FROM audio_chunks WHERE sid=? AND status IN ('queued','pending','working')",(sid,)).fetchone()[0]
            errors=db.execute("SELECT COUNT(*) FROM audio_chunks WHERE sid=? AND status='error'",(sid,)).fetchone()[0]
            row=db.execute("SELECT id,created,title,transcript,summary FROM conversations WHERE id=?",(sid,)).fetchone()
        if pending:return
        self.finishing.remove(sid)
        if errors:
            self.status.set("Some audio could not be transcribed. Original WAVs are saved; use Retry unfinished transcription.")
            return
        if not row or not row[3].strip():
            with self.core.connect() as db:db.execute("UPDATE voice_sessions SET state='ready' WHERE sid=?",(sid,))
            self.status.set("No speech detected. The recording is saved locally.")
            return
        with self.core.connect() as db:db.execute("UPDATE voice_sessions SET state='summarizing' WHERE sid=?",(sid,))
        def done(text):
            with self.core.connect() as db:
                db.execute("UPDATE conversations SET summary=? WHERE id=?",(text,sid))
                db.execute("UPDATE voice_sessions SET state='ready' WHERE sid=?",(sid,))
            self.core.refresh_profile("RECORDED CONVERSATION SUMMARY:\n"+text)
            try:
                import reminders
                if reminders.capture_followups(text):self.refresh_reminders()
            except Exception:pass
            self.display(self.summary,text);self.refresh();self.voice_answer(text)
            self.status.set(f"Saved transcript and summary for conversation [{sid}].")
        def failed(error):
            with self.core.connect() as db:db.execute("UPDATE voice_sessions SET state='summary_error' WHERE sid=?",(sid,))
            self.status.set("Transcript saved. Summary failed; start Ollama and use Retry unfinished summaries.")
        self.work(lambda:self.core.summarize(row),done,failed)

    def voice_poll(self):
        for _ in range(30):
            try:event=self.voice_events.get_nowait()
            except queue.Empty:break
            if event[0]=="voices":
                names=[v["name"] for v in event[1]]
                self.reply_voice["values"]=names
                saved=self.settings.get("voice","")
                if names:self.reply_voice.set(saved if saved in names else names[0])
            elif event[0]=='manual_progress':
                if getattr(self,'importing_manual',False):self.status.set(event[1])
            elif event[0]=="transcribed":
                sid=event[1]
                if sid==self.session_id:
                    with self.core.connect() as db:text=db.execute("SELECT transcript FROM conversations WHERE id=?",(sid,)).fetchone()[0]
                    self.display(self.live_transcript,text);self.live_transcript.see("end")
                self.refresh()
            elif event[0]=="question":
                _,token,text=event
                if token!=self.question_generation or self.closing:continue
                if not text.strip():
                    self.handsfree=False;self.heard.set("No speech detected. Check the selected microphone and level meter.")
                    self.status.set("Microphone off. No speech detected.");continue
                self.heard.set("You said: "+text);self.question.delete(0,"end");self.question.insert(0,text);self._voice_message=True;self.answer()
            elif event[0]=="question_error":
                if event[1]==self.question_generation:
                    self.handsfree=False;self.status.set("Question transcription failed: "+event[2])
            elif event[0]=="asr_error":
                self.status.set("Transcription failed; original audio retained. "+event[2])
            elif event[0]=="capture_error":
                self.handsfree=False
                if self.recorder:self.stop_recording()
                self.status.set(event[1])
            elif event[0]=="wake":
                # Stitch with the previous chunk's tail: the wake word can span a chunk boundary.
                prev=getattr(self,"_wake_prev","")
                combined=((prev+" "+event[1]).strip())[-160:]
                self._wake_prev=""
                m=self._wake_match(combined)
                if not m:
                    self._wake_prev=combined[-40:]
                    continue
                rest=combined[m.end():].strip(" \t,.!?")
                self._wake_confirm()
                self._wake_stop()
                if rest:
                    self.heard.set("You said: "+rest);self.question.delete(0,"end");self.question.insert(0,rest);self._voice_message=True;self.answer()
                elif self._can_start():
                    self.start_question();self.heard.set(f"'{self._wake_word().capitalize()}' heard — say your command.")
                else:self.status.set(f"'{self._wake_word().capitalize()}' heard, but the app is busy.")
            elif event[0]=="wake_error":
                if self.wake_recorder is not None:self.status.set(event[1])
            elif event[0]=="error":
                self.status.set(event[1]);self.handsfree=False
        speaking=self.speaker.active.is_set()
        if self.was_speaking and not speaking:self.next_listen=time.monotonic()+.65
        self.was_speaking=speaking
        rec=self.recorder
        if rec:
            elapsed=time.monotonic()-rec.started
            self.mic_state.set(("RECORDING · "+getattr(self,"active_source","Microphone") if self.record_kind=="conversation" else "LISTENING for question · Microphone")+" · "+timestamp(elapsed))
            self.mic_label.configure(foreground=ui_theme.DANGER if self.record_kind=="conversation" else ui_theme.ACCENT)
            self.meter["value"]=min(100,rec.level*650)
            self.mic.configure(state="disabled");self.language.configure(state="disabled")
            self.capture_source.configure(state="disabled");self.pc_output.configure(state="disabled")
            try:active=bool(rec.stream and rec.stream.active)
            except Exception as e:rec.error=str(e);active=False
            if not active:
                self.stop_recording()
            elif self.record_kind=="question" and (
                elapsed>=30 or (rec.last_voice is not None and time.monotonic()-rec.last_voice>=2 and elapsed>=2.5)):
                self.stop_recording()
        elif self.wake_recorder is not None:
            self.mic_state.set(f"WAKE · listening for '{self._wake_word()}' · Microphone")
            self.mic_label.configure(foreground=ui_theme.ACCENT)
            self.meter["value"]=min(100,self.wake_recorder.level*650)
            self.mic.configure(state="readonly");self.language.configure(state="readonly")
            self.capture_source.configure(state="readonly");self.pc_output.configure(state="readonly" if self.capture_source.get()!="Microphone" else "disabled")
            try:active=bool(self.wake_recorder.stream and self.wake_recorder.stream.active)
            except Exception:active=False
            if not active:self._wake_stop()
        else:
            self.mic_state.set("Speaking reply · Capture OFF" if self.speaker.active.is_set() else "Capture OFF")
            self.mic_label.configure(foreground=ui_theme.OK if self.speaker.active.is_set() else ui_theme.MUTED)
            self.meter["value"]=0;self.mic.configure(state="readonly");self.language.configure(state="readonly")
            self.capture_source.configure(state="readonly");self.pc_output.configure(state="readonly" if self.capture_source.get()!="Microphone" else "disabled")
        self.stop_button.configure(text="Stop & send" if self.record_kind=="question" else "Stop & summarize",style="Accent.TButton" if self.recorder else "TButton",state="normal" if self.recorder else "disabled")
        occupied=bool(self.recorder or self.busy or self.audio_jobs.unfinished_tasks or self.finishing or self.closing)
        self.record_button.configure(style="Danger.TButton" if (self.recorder and self.record_kind=="conversation") else "TButton",
            state="disabled" if occupied or self.handsfree else "normal")
        self.ask_voice_button.configure(state="disabled" if occupied or self.handsfree else "normal")
        self.chat_button.configure(text="Stop voice chat" if self.handsfree else "Start voice chat",
            state="normal" if self.handsfree or not occupied else "disabled")
        try:self.sync_main_mic()
        except Exception:pass
        self.audio_state.set(f"Local transcription queue: {self.audio_jobs.unfinished_tasks} · Whisper Small / CPU")
        self._finish_sessions()
        if self.wake_enabled.get() and not self.closing and self.wake_recorder is None and not (self.recorder or self.busy or self.audio_jobs.unfinished_tasks or self.finishing or self.speaker.active.is_set()):
            self._wake_start()
        if self.handsfree and not occupied and not self.speaker.active.is_set() and time.monotonic()>self.next_listen:
            self.start_question()
        if self.closing and not self.recorder and not self.busy and not self.audio_jobs.unfinished_tasks and not self.finishing:
            self.speaker.stop();self.root.destroy();return False
        return True

    def voice_close(self):
        self.handsfree=False;self.question_generation+=1;self.speaker.stop()
        self._wake_stop()
        if self.recorder:self.stop_recording()
        self.closing=True;self.voice_preferences()
        self.status.set("Finishing saved audio and transcription before closing…")




