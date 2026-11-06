# SPDX-License-Identifier: AGPL-3.0-or-later
"""WASAPI loopback and synchronized microphone/computer mixing."""
from collections import deque
import threading,time,queue
import numpy as np
import pyaudiowpatch as pa
from local_audio import Recorder,sd

DEFAULT_OUTPUT="Default playback output"

def loopback_outputs():
    with pa.PyAudio() as p:
        return [(int(d["index"]),d["name"]) for d in p.get_loopback_device_info_generator()]

class SampleBuffer:
    def __init__(self,limit=96000):
        self.parts=deque();self.count=0;self.limit=limit;self.lock=threading.Lock()
    def push(self,data):
        with self.lock:
            self.parts.append(data);self.count+=len(data)
            if self.count>self.limit:raise RuntimeError("Audio input backlog exceeded two seconds; recording stopped to avoid losing synchronization.")
    def take(self,size):
        result=np.zeros(size,dtype=np.float32);offset=0
        with self.lock:
            while self.parts and offset<size:
                part=self.parts.popleft();n=min(size-offset,len(part))
                result[offset:offset+n]=part[:n];offset+=n;self.count-=n
                if n<len(part):self.parts.appendleft(part[n:])
        return result

def mono_resample(raw,channels,source_rate,target_rate=48000):
    matrix=np.frombuffer(raw,dtype=np.float32).reshape(-1,channels)
    if channels<=2:data=matrix.mean(axis=1)
    else:
        # Preserve front-channel speech on 5.1/7.1 Sonar outputs instead of averaging silent channels.
        weights=np.array(([.5,.5,.707,0]+[.354]*max(0,channels-4))[:channels],dtype=np.float32)
        data=matrix@weights
    if source_rate==target_rate:return data
    count=round(len(data)*target_rate/source_rate)
    if not count:return np.empty(0,dtype=np.float32)
    # Shared-mode endpoints are opened in 10 ms blocks (441/480/960 frames).
    return np.interp(np.arange(count)*source_rate/target_rate,np.arange(len(data)),data).astype(np.float32)

def mix_samples(parts):
    if not parts:return np.empty(0,dtype=np.int16)
    return (np.clip(sum(parts)/len(parts),-1,1)*32767).astype(np.int16)

class LoopbackStream:
    def __init__(self,owner,microphone,mode,output_name):
        self.owner=owner;self.p=pa.PyAudio();self.streams=[];self.buffers=[]
        self.live=threading.Event();self.thread=None;self.failure=""
        try:
            if output_name==DEFAULT_OUTPUT:
                output=self.p.get_default_wasapi_loopback()
            else:
                output=next((d for d in self.p.get_loopback_device_info_generator() if d["name"]==output_name),None)
                if output is None:raise RuntimeError("The selected playback output is no longer available. Refresh audio devices.")
            self.output_name=output["name"]
            self._open(output)
            if mode=="Both":
                name=sd.query_devices(microphone,"input")["name"]
                devices=[d for d in self.p.get_device_info_generator() if d["maxInputChannels"] and not d.get("isLoopbackDevice")
                    and self.p.get_host_api_info_by_index(d["hostApi"])["type"]==pa.paWASAPI]
                device=next((d for d in devices if d["name"]==name),None)
                if device is None:device=next((d for d in devices if d["name"].startswith(name)),None)
                if device is None:raise RuntimeError("No matching WASAPI microphone. Select a microphone marked Windows WASAPI.")
                self._open(device)
        except Exception:
            self.close();raise
    def _open(self,device):
        rate=int(device["defaultSampleRate"]);channels=int(device["maxInputChannels"])
        buffer=SampleBuffer();self.buffers.append(buffer)
        def callback(raw,frames,timing,status):
            if status:
                self.owner.input_overflows+=1
                self.owner.error=f"Audio input warning (PortAudio status {status})."
            try:
                buffer.push(mono_resample(raw,channels,rate))
                return None,pa.paContinue
            except Exception as e:
                self.failure=str(e);return None,pa.paAbort
        stream=self.p.open(format=pa.paFloat32,channels=channels,rate=rate,input=True,
            input_device_index=int(device["index"]),frames_per_buffer=max(1,rate//100),
            stream_callback=callback,start=False)
        self.streams.append(stream)
    @property
    def active(self):return self.live.is_set() and bool(self.thread and self.thread.is_alive())
    def start(self):
        for stream in self.streams:stream.start_stream()
        self.live.set()
        def run():
            try:
                # Small startup cushion aligns independent shared-mode endpoints.
                deadline=time.monotonic()+.15
                while self.live.is_set() and time.monotonic()<deadline:time.sleep(.005)
                while self.live.is_set():
                    if self.failure:raise RuntimeError(self.failure)
                    if any(not stream.is_active() for stream in self.streams):raise RuntimeError("An audio device stopped or disconnected.")
                    samples=mix_samples([buffer.take(960) for buffer in self.buffers])
                    normalized=samples.astype(np.float32)/32768
                    self.owner.level=float(np.sqrt(np.mean(normalized*normalized)))
                    if self.owner.level>.008:self.owner.last_voice=time.monotonic()
                    try:self.owner.blocks.put_nowait((samples.tobytes(),time.monotonic()-self.owner.started))
                    except queue.Full:raise RuntimeError("Audio disk-writer buffer is full.")
                    deadline+=.02
                    delay=deadline-time.monotonic()
                    if delay<-.5:raise RuntimeError("Audio capture fell behind. Stop other intensive work and retry.")
                    if delay>0:
                        until=time.monotonic()+delay
                        while self.live.is_set() and time.monotonic()<until:time.sleep(min(.005,max(0,until-time.monotonic())))
            except Exception as e:
                self.owner.error=str(e);self.owner.on_error(str(e))
            finally:self.live.clear()
        self.thread=threading.Thread(target=run,daemon=True);self.thread.start()
    def stop(self):
        self.live.clear()
        if self.thread:self.thread.join(3)
        for stream in self.streams:
            try:
                if stream.is_active():stream.stop_stream()
            except OSError:pass
    def close(self):
        self.stop()
        for stream in self.streams:
            try:stream.close()
            except OSError:pass
        self.streams=[]
        if self.p:self.p.terminate();self.p=None

class ComputerRecorder(Recorder):
    def __init__(self,folder,device,on_chunk,on_error=lambda message:None,chunk_seconds=15,
                 mode="Computer audio",output_name=DEFAULT_OUTPUT):
        super().__init__(folder,device,on_chunk,on_error,chunk_seconds)
        if mode not in ("Computer audio","Both"):raise ValueError("Unknown computer-audio capture mode")
        self.mode=mode;self.output_name=output_name
    def start(self):
        self.folder.mkdir(parents=True,exist_ok=True);self.rate=48000
        self.stream=LoopbackStream(self,self.device,self.mode,self.output_name)
        self.output_name=self.stream.output_name
        self.started=time.monotonic()
        self.writer=threading.Thread(target=self._write,daemon=True);self.writer.start()
        try:self.stream.start();self.running=True
        except Exception:
            self.stream.close();self.stream=None;self.blocks.put(None);self.writer.join(5);raise

