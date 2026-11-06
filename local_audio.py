# SPDX-License-Identifier: AGPL-3.0-or-later
"""Local microphone capture, offline Whisper, and neural speech (MeloTTS, Piper, SAPI fallback)."""
import base64, json, os, queue, re, subprocess, tempfile, threading, time, wave
from pathlib import Path
import numpy as np
import sounddevice as sd

def microphones():
    hosts=sd.query_hostapis()
    result=[]
    for i,d in enumerate(sd.query_devices()):
        if d["max_input_channels"]>0 and hosts[d["hostapi"]]["name"] in ("MME","Windows WASAPI"):
            result.append((i,f'{d["name"]} [{hosts[d["hostapi"]]["name"]}]'))
    return result

def powershell(script, **kwargs):
    encoded=base64.b64encode(script.encode("utf-16-le")).decode("ascii")
    return subprocess.Popen(["powershell.exe","-NoProfile","-NonInteractive","-EncodedCommand",encoded],
        creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),**kwargs)

import piper_speech
import melo_speech

def _ps(value):return base64.b64encode(str(value).encode("utf-8")).decode()

AUTO_VOICE="Automatic · English / Español (México)"

def apply_pending_voice(config_path,settings):
    """Apply a voice selected by setup after the previous app instance has exited."""
    config_path=Path(config_path);pending=config_path.with_name("pending-voice.json")
    if not pending.is_file():return settings
    selected=json.loads(pending.read_text(encoding="utf-8"))["voice"]
    if not isinstance(selected,str) or not selected.strip():raise ValueError("Invalid pending voice")
    updated=dict(settings,voice=selected)
    temp=config_path.with_suffix(".tmp")
    temp.write_text(json.dumps(updated,indent=2),encoding="utf-8");temp.replace(config_path)
    pending.unlink()
    return updated

def speech_language(text):
    # Short greetings often contain too few words for the ASR language heuristic.
    if re.search(r"\b(hola|gracias|buenos|buenas|puedes|necesitas|recordatorio|español|claro|entendido|perfecto)\b|\b(de acuerdo|por supuesto|hasta luego)\b",text,re.I):return "es"
    return _guess_language(text)

def language_fallback(language):
    candidates=piper_speech.voice_list() if piper_speech.available() else []
    if language=="es":
        preferred=next((v for v in candidates if v["key"]=="es_MX-claude-high"),None)
        if preferred:return preferred["name"]
    return next((v["name"] for v in candidates if v["culture"].lower().startswith(language)),"")

def resolve_voice(text,voice):
    if voice!=AUTO_VOICE:return voice
    if speech_language(text)=="es":
        return language_fallback("es") or "MeloTTS · Spanish"
    return "MeloTTS · English (US)"

def voices():
    result=[]
    if melo_speech.available():
        result.extend(melo_speech.VOICES)
    if piper_speech.available():
        result.extend(piper_speech.voice_list())
    if result:return [{"name":AUTO_VOICE,"culture":"auto"}]+result
    script="""[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Speech
$s=[System.Speech.Synthesis.SpeechSynthesizer]::new()
@($s.GetInstalledVoices() | ForEach-Object { @{name=$_.VoiceInfo.Name; culture=$_.VoiceInfo.Culture.Name} }) | ConvertTo-Json -Compress
$s.Dispose()
"""
    p=powershell(script,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    out,err=p.communicate(timeout=20)
    if p.returncode: raise RuntimeError(err.decode("utf-8","replace"))
    data=json.loads(out.decode("utf-8-sig"))
    return data if isinstance(data,list) else [data]

def speech_script(text,voice="",wav=None):
    target=wav or os.path.join(tempfile.gettempdir(),"omi-reply-"+str(os.getpid())+"-"+str(time.monotonic_ns())+".wav")
    if piper_speech.available():
        v=piper_speech.find_voice(voice)
        if v:
            play="" if wav else "Add-Type -AssemblyName System;(New-Object Media.SoundPlayer $wav).PlaySync();Remove-Item $wav -Force;"
            return ("$ErrorActionPreference='Stop';$OutputEncoding=[Text.UTF8Encoding]::new($false);"
                "$exe=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+_ps(piper_speech.exe_path())+"'));"
                "$model=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+_ps(piper_speech.VOICES_DIR/(v["key"]+".onnx"))+"'));"
                "$cfg=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+_ps(piper_speech.VOICES_DIR/(v["key"]+".onnx.json"))+"'));"
                "$wav=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+_ps(target)+"'));"
                "$t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+_ps(text)+"'));"
                "$t|& $exe --model $model --config $cfg --output_file $wav|Out-Null;"
                "if(-not (Test-Path $wav)){throw 'Neural speech synthesis produced no audio'};"
                +play)
    script="$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Speech; $s=[System.Speech.Synthesis.SpeechSynthesizer]::new();"
    if voice:
        script+=f"$s.SelectVoice([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{_ps(voice)}')));"
    if wav:
        script+=f"$s.SetOutputToWaveFile([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{_ps(wav)}')));"
    else:script+="$s.SetOutputToDefaultAudioDevice();"
    script+=f"try {{$s.Speak([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{_ps(text)}')))}} finally {{$s.Dispose()}}"
    return script

def playback_script(wav):
    return ("$ErrorActionPreference='Stop';"
        "$wav=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('"+_ps(wav)+"'));"
        "(New-Object Media.SoundPlayer $wav).PlaySync();Remove-Item -LiteralPath $wav -Force;")

def speak_to_wav(text,path,voice=""):
    voice=resolve_voice(text,voice)
    if voice.startswith("MeloTTS"):
        melo_speech.synth(text,voice,path);return
    p=powershell(speech_script(text,voice,path),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    _,err=p.communicate(timeout=90)
    if p.returncode:raise RuntimeError(err.decode("utf-8","replace"))

def spoken_text(text):
    text=re.sub(r"\[[0-9]+\]","",text)
    text=re.sub(r"https?://\S+","",text)
    return re.sub(r"[#*_]", "",text).replace(chr(96),"").strip()

class Speaker:
    def __init__(self,on_error=lambda message:None):
        self.active=threading.Event(); self.lock=threading.Lock()
        self.proc=None; self.generation=0; self.on_error=on_error
    def stop(self):
        with self.lock:
            self.generation+=1
            if self.proc and self.proc.poll() is None:self.proc.terminate()
            self.proc=None;self.active.clear()
    def say(self,text,voice=""):
        self.stop()
        text=spoken_text(text)
        if not text:return
        voice=resolve_voice(text,voice)
        with self.lock:token=self.generation;self.active.set()
        def run():
            wav=None
            try:
                if voice.startswith("MeloTTS"):
                    try:
                        fd,wav=tempfile.mkstemp(suffix=".wav",prefix="omi-melo-");os.close(fd)
                        melo_speech.synth(text,voice,wav)
                        script=playback_script(wav)
                    except Exception as e:
                        self.on_error("MeloTTS failed ("+str(e)+"); using fallback voice.")
                        language="es" if "Spanish" in voice else "en"
                        script=speech_script(text,language_fallback(language))
                else:
                    script=speech_script(text,voice)
                with self.lock:
                    if token!=self.generation:return
                    p=powershell(script,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE)
                    self.proc=p
                _,err=p.communicate()
                if p.returncode and token==self.generation:
                    self.on_error(err.decode("utf-8","replace") or "Windows voice playback failed")
            except Exception as e:self.on_error(str(e))
            finally:
                if wav:
                    try:os.unlink(wav)
                    except OSError:pass
                with self.lock:
                    if token==self.generation:self.proc=None;self.active.clear()
        threading.Thread(target=run,daemon=True).start()

class Recorder:
    """A bounded callback queue and disk writer; audio survives transcription failures."""
    def __init__(self,folder,device,on_chunk,on_error=lambda message:None,chunk_seconds=15):
        self.folder=Path(folder);self.device=device;self.on_chunk=on_chunk;self.on_error=on_error
        self.chunk_seconds=chunk_seconds;self.blocks=queue.Queue(maxsize=128)
        self.stream=None;self.writer=None;self.running=False;self.level=0.;self.last_voice=None
        self.started=0.;self.error="";self.files=[];self.input_overflows=0
    def start(self):
        self.folder.mkdir(parents=True,exist_ok=True)
        info=sd.query_devices(self.device,"input")
        self.rate=int(info["default_samplerate"])
        sd.check_input_settings(device=self.device,channels=1,dtype="int16",samplerate=self.rate)
        self.started=time.monotonic()
        def callback(data,frames,timing,status):
            if status:
                self.input_overflows+=1
                self.error=f"Audio input warning: {status}"
            raw=bytes(data);samples=np.frombuffer(raw,dtype=np.int16).astype(np.float32)
            self.level=float(np.sqrt(np.mean(samples*samples))/32768) if samples.size else 0.
            if self.level>getattr(self,"vad_threshold",.008):self.last_voice=time.monotonic()
            try:self.blocks.put_nowait((raw,time.monotonic()-self.started))
            except queue.Full:
                self.error="Microphone buffer overflow. Stop recording; saved audio is retained."
                raise sd.CallbackAbort
        self.stream=sd.RawInputStream(device=self.device,samplerate=self.rate,channels=1,
            dtype="int16",blocksize=max(1,self.rate//10),callback=callback)
        self.writer=threading.Thread(target=self._write,daemon=True)
        self.writer.start()
        try:self.stream.start();self.running=True
        except Exception:
            self.stream.close();self.stream=None;self.blocks.put(None);self.writer.join(5);raise
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
                if frames>=self.rate*self.chunk_seconds:flush()
            flush()
        except Exception as e:self.error=str(e);self.on_error(f"Recording save failed: {e}")
    def stop(self):
        self.running=False
        if self.stream:
            try:self.stream.stop()
            finally:self.stream.close();self.stream=None
        if self.writer and self.writer.is_alive():
            self.blocks.put(None,timeout=5);self.writer.join(10)
            if self.writer.is_alive():raise RuntimeError("Audio writer is still saving. Do not close the app yet.")
        return self.files

PARAKEET_GGUF="parakeet/parakeet-tdt-0.6b-v3-Q6_K.gguf"

def _guess_language(text):
    t=" "+text.lower()+" "
    if any(c in t for c in "áéíóúñü¿¡"):return "es"
    es_words=(" que "," de la "," de el "," mi "," me "," te "," se "," para "," con "," como ",
        " los "," las "," una "," estoy "," necesito "," recuerda"," mañana"," gracias"," hola",
        " también"," está "," quiero "," llamar")
    hits=sum(1 for w in es_words if w in t)
    return "es" if hits>=2 else "en"

class Transcriber:
    def __init__(self,model_root):
        self.model_root=Path(model_root);self.model=None;self.lock=threading.Lock()
        self._pk=None;self._pk_broken=False
    def _parakeet(self):
        if self._pk is None:
            import transcribe_cpp as tc
            gguf=self.model_root/PARAKEET_GGUF
            if not gguf.exists():raise FileNotFoundError(f"Parakeet model missing: {gguf}")
            self._pk=tc.Model(str(gguf),backend="cpu")
        return self._pk
    @staticmethod
    def _load_pcm_16k(path):
        with wave.open(str(path),"rb") as w:
            ch,sw,fr=w.getnchannels(),w.getsampwidth(),w.getframerate()
            raw=w.readframes(w.getnframes())
        if sw==2:a=np.frombuffer(raw,dtype=np.int16)
        elif sw==4:a=(np.frombuffer(raw,dtype=np.int32)>>16).astype(np.int16)
        elif sw==1:a=(np.frombuffer(raw,dtype=np.uint8).astype(np.int16)-128)*256
        else:raise ValueError(f"unsupported sample width {sw}")
        if ch>1:a=a.reshape(-1,ch).mean(axis=1)
        pcm=a.astype(np.float32)/32768.0
        if fr!=16000:
            n=int(round(len(pcm)*16000.0/fr))
            pcm=np.interp(np.linspace(0,len(pcm),n,endpoint=False),
                np.arange(len(pcm),dtype=np.float32),pcm).astype(np.float32)
        return np.ascontiguousarray(pcm)
    def _transcribe_parakeet(self,path,language):
        import transcribe_cpp as tc
        model=self._parakeet();pcm=self._load_pcm_16k(path)
        res=tc.transcribe(model,pcm,backend="cpu",language=language)
        segs=[{"start":s.t0_ms/1000.0,"end":s.t1_ms/1000.0,"text":s.text.strip()}
              for s in res.segments if s.text.strip()]
        text=res.text.strip()
        if not segs and text:segs=[{"start":0.0,"end":len(pcm)/16000.0,"text":text}]
        detected=(res.language or "").strip() or language or _guess_language(text)
        return segs,detected
    def _transcribe_whisper(self,path,language):
        if self.model is None:
            from faster_whisper import WhisperModel
            self.model=WhisperModel("small",device="cpu",compute_type="int8",
                cpu_threads=min(6,os.cpu_count() or 4),download_root=str(self.model_root),local_files_only=True)
        segments,info=self.model.transcribe(str(path),language=language,beam_size=3,
            vad_filter=True,vad_parameters={"min_silence_duration_ms":500},
            condition_on_previous_text=False)
        result=[{"start":s.start,"end":s.end,"text":s.text.strip()} for s in segments if s.text.strip()]
        return result,info.language
    def transcribe(self,path,language=None):
        with self.lock:
            if not self._pk_broken:
                try:return self._transcribe_parakeet(path,language)
                except Exception as e:
                    self._pk_broken=True
                    print(f"[transcriber] parakeet failed ({e}); falling back to whisper",flush=True)
            return self._transcribe_whisper(path,language)

def timestamp(seconds):
    seconds=max(0,int(seconds));return f"{seconds//3600:02d}:{seconds//60%60:02d}:{seconds%60:02d}"

