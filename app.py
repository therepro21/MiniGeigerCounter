#!/usr/bin/env python3
"""MiniGeigerCounter - audio pulse acquisition, web UI and MQTT."""
import asyncio, json, os, sqlite3, threading, time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
import numpy as np
import sounddevice as sd
import paho.mqtt.client as mqtt
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

DATA = Path(os.environ.get("MINIGEIGER_DATA", Path(__file__).parent / "data")); DATA.mkdir(parents=True, exist_ok=True)
CONFIG_FILE, DB_FILE = DATA / "config.json", DATA / "history.sqlite3"
DEFAULT = {"audio_device":None,"sample_rate":44100,"threshold":.16,"holdoff_ms":80,"counts_per_usvh":11.26,"mqtt_enabled":False,"mqtt_host":"127.0.0.1","mqtt_port":1883,"mqtt_username":"","mqtt_password":"","mqtt_topic":"minigeiger","home_assistant_discovery":True,"web_port":8734}
def config():
    if not CONFIG_FILE.exists(): CONFIG_FILE.write_text(json.dumps(DEFAULT, indent=2))
    return {**DEFAULT, **json.loads(CONFIG_FILE.read_text())}
def save_config(c): CONFIG_FILE.write_text(json.dumps({**DEFAULT,**c}, indent=2))
def db():
    con=sqlite3.connect(DB_FILE); con.execute("CREATE TABLE IF NOT EXISTS samples (ts INTEGER PRIMARY KEY, cpm REAL, usvh REAL, total INTEGER)"); return con

class Monitor:
    def __init__(self):
        self.lock=threading.Lock(); self.pulses=deque(); self.total=0; self.peak=0.; self.last_pulse=0.; self.stream=None; self.device_name="kein Eingang"; self.ws=[]; self.mqtt=None; self._load_total()
    def _load_total(self):
        with db() as c:
            row=c.execute("SELECT total FROM samples ORDER BY ts DESC LIMIT 1").fetchone(); self.total=row[0] if row else 0
    def callback(self, indata, frames, timing, status):
        now=time.time(); peak=float(np.max(np.abs(indata)))
        with self.lock:
            self.peak=peak; c=config()
            if peak>=float(c['threshold']) and (now-self.last_pulse)*1000>=float(c['holdoff_ms']):
                self.last_pulse=now; self.pulses.append(now); self.total+=1
            while self.pulses and self.pulses[0]<now-3600: self.pulses.popleft()
    def restart_audio(self):
        if self.stream: self.stream.stop(); self.stream.close(); self.stream=None
        c=config(); dev=c['audio_device']
        if dev is None: self.device_name="kein Eingang"; return
        try:
            info=sd.query_devices(int(dev), 'input'); self.device_name=info['name']
            self.stream=sd.InputStream(device=int(dev), channels=1, samplerate=int(c['sample_rate']), callback=self.callback, dtype='float32'); self.stream.start()
        except Exception as e: self.device_name=f"Audiofehler: {e}"
    def state(self):
        now=time.time()
        with self.lock:
            m=sum(p>=now-60 for p in self.pulses); smooth=sum(p>=now-300 for p in self.pulses)/5; c=config()
            return {"cpm":m,"smooth_cpm":smooth,"usvh":m/float(c['counts_per_usvh']),"total_count":self.total,"audio_peak":self.peak,"threshold":c['threshold'],"device_name":self.device_name,"timestamp":now}
    def publish(self, s):
        c=config()
        if not c['mqtt_enabled']: return
        try:
            if not self.mqtt:
                self.mqtt=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="minigeigercounter");
                if c['mqtt_username']: self.mqtt.username_pw_set(c['mqtt_username'],c['mqtt_password'])
                self.mqtt.will_set(c['mqtt_topic']+'/status','offline',retain=True); self.mqtt.connect_async(c['mqtt_host'],int(c['mqtt_port']),60); self.mqtt.loop_start()
            base=c['mqtt_topic'].rstrip('/');
            for k,v in [('radiation_usvh',s['usvh']),('cpm',s['cpm']),('count_total',s['total_count'])]: self.mqtt.publish(f'{base}/{k}',str(v),retain=True)
            self.mqtt.publish(f'{base}/status','online',retain=True)
            if c['home_assistant_discovery']:
                for key,name,unit,cls in [('radiation_usvh','Radiation','µSv/h','measurement'),('cpm','Radiation CPM','CPM','measurement')]:
                    payload={"name":name,"state_topic":f'{base}/{key}',"unique_id":f'minigeigercounter_{key}',"unit_of_measurement":unit,"state_class":cls,"device":{"identifiers":["minigeigercounter"],"name":"MiniGeigerCounter","model":"FTLab SGP001 audio"}}
                    self.mqtt.publish(f'homeassistant/sensor/minigeigercounter_{key}/config',json.dumps(payload),retain=True)
        except Exception: pass

monitor=Monitor()
async def worker():
    last_store=0
    while True:
        s=monitor.state(); monitor.publish(s)
        for ws in monitor.ws[:]:
            try: await ws.send_json(s)
            except Exception: monitor.ws.remove(ws)
        if time.time()-last_store>=300:
            with db() as c: c.execute('INSERT OR REPLACE INTO samples VALUES(?,?,?,?)',(int(time.time()),s['smooth_cpm'],s['smooth_cpm']/float(config()['counts_per_usvh']),s['total_count']))
            last_store=time.time()
        await asyncio.sleep(2)
@asynccontextmanager
async def lifespan(app):
    monitor.restart_audio(); task=asyncio.create_task(worker()); yield; task.cancel()
app=FastAPI(title='MiniGeigerCounter',lifespan=lifespan)
app.mount('/static',StaticFiles(directory=Path(__file__).parent/'static'),name='static')
@app.get('/')
async def home(): return FileResponse(Path(__file__).parent/'static'/'index.html')
@app.get('/api/state')
async def state(): return monitor.state()
@app.get('/api/devices')
async def devices():
    return [{"id":i,"name":d['name']} for i,d in enumerate(sd.query_devices()) if d['max_input_channels']>0]
@app.get('/api/config')
async def get_config(): return config()
@app.put('/api/config')
async def put_config(update:dict):
    allowed=set(DEFAULT); c={**config(),**{k:v for k,v in update.items() if k in allowed}}; save_config(c); monitor.mqtt=None; monitor.restart_audio(); return {"ok":True}
@app.get('/api/history')
async def history(hours:int=24):
    hours=max(1,min(hours,24*90)); since=int(time.time()-hours*3600)
    with db() as c: rows=c.execute('SELECT ts,usvh FROM samples WHERE ts>=? ORDER BY ts',(since,)).fetchall()
    return [{"ts":r[0],"usvh":r[1]} for r in rows]
@app.websocket('/ws')
async def websocket(ws:WebSocket):
    await ws.accept(); monitor.ws.append(ws)
    try:
        while True: await ws.receive_text()
    except Exception:
        if ws in monitor.ws: monitor.ws.remove(ws)
if __name__=='__main__': uvicorn.run(app,host='0.0.0.0',port=int(config()['web_port']))
