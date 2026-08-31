#!/usr/bin/env python3
"""MiniGeigerCounter - audio pulse acquisition, web UI and MQTT."""
import asyncio, io, json, os, sqlite3, threading, time, subprocess, urllib.request
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path
import numpy as np
import sounddevice as sd
import paho.mqtt.client as mqtt
try:
    from gpiozero import DigitalInputDevice
    GPIO_AVAILABLE=True
except ImportError:
    GPIO_AVAILABLE=False
from fastapi import FastAPI, HTTPException, WebSocket
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
import uvicorn
try:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE=True
except ImportError:
    REPORTLAB_AVAILABLE=False

APP_VERSION = "3.1.1"
UPDATE_VERSION_URLS = (
    "https://raw.githubusercontent.com/therepro21/MiniGeigerCounter/main/VERSION",
    "https://api.github.com/repos/therepro21/MiniGeigerCounter/tags?per_page=20",
)
DATA = Path(os.environ.get("MINIGEIGER_DATA", Path(__file__).parent / "data")); DATA.mkdir(parents=True, exist_ok=True)
CONFIG_FILE, DB_FILE = DATA / "config.json", DATA / "history.sqlite3"
DEFAULT = {"counter_type":"sgp001_audio","audio_device":None,"sample_rate":44100,"gpio_pin":17,"gpio_active_low":True,"threshold":.0071,"holdoff_ms":80,"counts_per_usvh":8014.285714,"cps_per_usvh":133.571429,"click_sound_enabled":True,"history_retention_days":0,"mqtt_enabled":False,"mqtt_host":"192.168.0.31","mqtt_port":1883,"mqtt_username":"minigeigercounter","mqtt_password":"","mqtt_topic":"minigeiger","mqtt_discovery_prefix":"homeassistant","home_assistant_discovery":True,"update_token":"","web_port":8734}
RATE_PERIODS = (("1min", 60), ("5min", 300), ("15min", 900), ("1h", 3600), ("4h", 14400), ("12h", 43200), ("24h", 86400))
def config():
    if not CONFIG_FILE.exists(): CONFIG_FILE.write_text(json.dumps(DEFAULT, indent=2))
    stored=json.loads(CONFIG_FILE.read_text())
    # Migrate the former placeholder calibration to the supplied reference.
    if stored.get('counts_per_usvh') == 11.26:
        stored['counts_per_usvh']=DEFAULT['counts_per_usvh']; stored['cps_per_usvh']=DEFAULT['cps_per_usvh']; CONFIG_FILE.write_text(json.dumps({**DEFAULT,**stored}, indent=2))
    if '_click_default_v2' not in stored:
        stored['click_sound_enabled']=True; stored['_click_default_v2']=True; CONFIG_FILE.write_text(json.dumps({**DEFAULT,**stored}, indent=2))
    return {**DEFAULT, **stored}
def save_config(c): CONFIG_FILE.write_text(json.dumps({**DEFAULT,**c}, indent=2))
def db():
    con=sqlite3.connect(DB_FILE); con.execute("CREATE TABLE IF NOT EXISTS samples (ts INTEGER PRIMARY KEY, cpm REAL, usvh REAL, total INTEGER)"); return con

class Monitor:
    def __init__(self):
        self.lock=threading.Lock(); self.cfg=config(); self.config_dirty=False; self.pulses=deque(); self.period_buckets={key:deque() for key,_ in RATE_PERIODS}; self.period_counts={key:0 for key,_ in RATE_PERIODS}; self.rates_cache={key:0. for key,_ in RATE_PERIODS}; self.last_rate_state=0.; self.levels=deque(); self.level_seconds=deque(); self.level_ranges={}; self.last_level_state=0.; self.last_long_level_state=0.; self.total=0; self.peak=0.; self.rms=0.; self.sample_rate=0; self.last_pulse=0.; self.stream=None; self.gpio_input=None; self.device_name="kein Eingang"; self.ws=[]; self.mqtt=None; self.started_at=time.time(); self._load_total(); self._load_history_baselines()
    def _load_total(self):
        with db() as c:
            row=c.execute("SELECT total FROM samples ORDER BY ts DESC LIMIT 1").fetchone(); self.total=row[0] if row else 0
    def _load_history_baselines(self):
        """Load long-window baselines once; never query SQLite from the live path."""
        now=int(self.started_at); self.history_baselines={}
        with db() as c:
            for key,seconds in RATE_PERIODS:
                row=c.execute('SELECT ts,total FROM samples WHERE ts<=? ORDER BY ts DESC LIMIT 1',(now-seconds,)).fetchone()
                if row: self.history_baselines[key]=row
    def apply_config(self, c):
        with self.lock:
            self.cfg=c; self.config_dirty=True
    def flush_config(self):
        """Persist at most once per aggregation cycle; live acquisition uses RAM."""
        with self.lock:
            if not self.config_dirty: return
            pending=dict(self.cfg); self.config_dirty=False
        save_config(pending)
    def _register_pulse(self, now, c):
        if (now-self.last_pulse)*1000<float(c['holdoff_ms']): return
        self.last_pulse=now; self.pulses.append(now); self.total+=1; second=int(now)
        for key,_ in RATE_PERIODS:
            bucket=self.period_buckets[key]
            if bucket and bucket[-1][0]==second: bucket[-1]=(second,bucket[-1][1]+1)
            else: bucket.append((second,1))
            self.period_counts[key]+=1
    def _gpio_pulse(self):
        with self.lock: self._register_pulse(time.time(),self.cfg)
    def callback(self, indata, frames, timing, status):
        now=time.time(); peak=float(np.max(np.abs(indata)))
        with self.lock:
            self.peak=peak; self.rms=float(np.sqrt(np.mean(np.square(indata)))); self.levels.append((now,peak)); c=self.cfg; second=int(now)
            if self.level_seconds and self.level_seconds[-1][0]==second:
                _,low,high=self.level_seconds[-1]; self.level_seconds[-1]=(second,min(low,peak),max(high,peak))
            else: self.level_seconds.append((second,peak,peak))
            while self.levels and self.levels[0][0]<now-300: self.levels.popleft()
            while self.level_seconds and self.level_seconds[0][0]<second-86400: self.level_seconds.popleft()
            if peak>=float(c['threshold']): self._register_pulse(now,c)
            # Raw timestamps exist only for the exact, un-smoothed CPS display.
            while self.pulses and self.pulses[0]<now-1: self.pulses.popleft()
    def stop_input(self):
        if self.stream:
            self.stream.stop(); self.stream.close(); self.stream=None
        if self.gpio_input:
            self.gpio_input.close(); self.gpio_input=None
    def restart_input(self):
        self.stop_input(); c=self.cfg
        if c['counter_type'].endswith('_gpio'): return self.restart_gpio()
        return self.restart_audio()
    def restart_audio(self):
        c=self.cfg; dev=c['audio_device']
        if dev is None: self.device_name="kein Eingang"; return
        try:
            info=sd.query_devices(int(dev), 'input'); self.device_name=info['name']
            rates=[]
            for rate in (c.get('sample_rate', 44100), info['default_samplerate'], 48000, 44100):
                rate=int(round(float(rate)))
                if rate > 0 and rate not in rates: rates.append(rate)
            errors=[]
            for rate in rates:
                try:
                    self.stream=sd.InputStream(device=int(dev), channels=1, samplerate=rate, callback=self.callback, dtype='float32'); self.stream.start(); self.sample_rate=rate; return
                except Exception as e:
                    errors.append(str(e)); self.stream=None
            raise RuntimeError('; '.join(errors))
        except Exception as e: self.device_name=f"Audiofehler: {e}"
    def restart_gpio(self):
        c=self.cfg
        if not GPIO_AVAILABLE:
            self.device_name="GPIO Fehler: gpiozero fehlt - Installer erneut ausführen"; return
        try:
            pin=int(c['gpio_pin']); self.gpio_input=DigitalInputDevice(pin,pull_up=False)
            if c.get('gpio_active_low',True): self.gpio_input.when_deactivated=self._gpio_pulse; edge="fallende Flanke"
            else: self.gpio_input.when_activated=self._gpio_pulse; edge="steigende Flanke"
            self.sample_rate=0; self.device_name=f"GPIO BCM {pin} ({edge})"
        except Exception as e: self.device_name=f"GPIO Fehler: {e}"
    def state(self):
        now=time.time()
        with self.lock:
            c=self.cfg; second=int(now)
            if second!=self.last_rate_state:
                elapsed=now-self.started_at
                for key,seconds in RATE_PERIODS:
                    bucket=self.period_buckets[key]; cutoff=second-seconds
                    while bucket and bucket[0][0]<cutoff: self.period_counts[key]-=bucket.popleft()[1]
                    baseline=self.history_baselines.get(key)
                    if elapsed<seconds and baseline:
                        self.rates_cache[key]=max(0,self.total-baseline[1])/max((now-baseline[0])/60,1/60)
                    else:
                        self.rates_cache[key]=self.period_counts[key]/(min(elapsed,seconds)/60)
                self.last_rate_state=second
            rates=self.rates_cache; m=rates['1min']; smooth=rates['5min']
            # Deliberately un-smoothed: pulses detected during the rolling last second.
            current_cps=sum(p >= now-1 for p in self.pulses)
            if now-self.last_level_state>=1:
                level_ranges={}
                for label,seconds in (("5s",5),("30s",30),("1min",60),("5min",300)):
                    values=[value for ts,value in self.levels if ts>=now-seconds]
                    level_ranges[label]={"min":min(values) if values else 0,"max":max(values) if values else 0}
                self.level_ranges.update(level_ranges); self.last_level_state=now
            # Long audio ranges do not need a 10 Hz refresh; update them every 10 seconds.
            if now-self.last_long_level_state>=10:
                level_ranges={}
                for label,seconds in (("15min",900),("1h",3600),("4h",14400),("12h",43200),("24h",86400)):
                    values=[(low,high) for ts,low,high in self.level_seconds if ts>=now-seconds]
                    level_ranges[label]={"min":min((v[0] for v in values),default=0),"max":max((v[1] for v in values),default=0)}
                self.level_ranges.update(level_ranges); self.last_long_level_state=now
            return {"cpm":m,"cps":m/60,"current_cps":current_cps,"smooth_cpm":smooth,"rates_cpm":rates,"usvh":m/float(c['counts_per_usvh']),"counts_per_usvh":c['counts_per_usvh'],"cps_per_usvh":c['cps_per_usvh'],"total_count":self.total,"audio_peak":self.peak,"audio_rms":self.rms,"level_ranges":self.level_ranges,"sample_rate":self.sample_rate,"threshold":c['threshold'],"device_name":self.device_name,"database_bytes":DB_FILE.stat().st_size if DB_FILE.exists() else 0,"timestamp":now}
    def publish(self, s):
        c=self.cfg
        if not c['mqtt_enabled']: return
        try:
            if not self.mqtt:
                self.mqtt=mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="minigeigercounter");
                if c['mqtt_username']: self.mqtt.username_pw_set(c['mqtt_username'],c['mqtt_password'])
                self.mqtt.will_set(c['mqtt_topic'].rstrip('/')+'/status','offline',retain=True); self.mqtt.connect_async(c['mqtt_host'],int(c['mqtt_port']),60); self.mqtt.loop_start()
            base=c['mqtt_topic'].rstrip('/');
            discovery_prefix=c.get('mqtt_discovery_prefix','homeassistant').strip('/') or 'homeassistant'
            for k,v in [('radiation_usvh',s['usvh']),('cpm',s['cpm']),('cps',s['current_cps']),('count_total',s['total_count'])]: self.mqtt.publish(f'{base}/{k}',str(v),retain=True)
            self.mqtt.publish(f'{base}/status','online',retain=True)
            if c['home_assistant_discovery']:
                sensors=[('radiation_usvh','Dosisleistung','sensor.minigeigercounter_dosisleistung','µSv/h','measurement','mdi:radioactive'),('cpm','Impulse pro Minute','sensor.minigeigercounter_cpm','CPM','measurement','mdi:counter'),('cps','Impulse pro Sekunde','sensor.minigeigercounter_cps','CPS','measurement','mdi:counter'),('count_total','Impulse gesamt','sensor.minigeigercounter_impulse_gesamt',None,'total_increasing','mdi:counter')]
                for key,name,entity_id,unit,cls,icon in sensors:
                    payload={"name":name,"default_entity_id":entity_id,"state_topic":f'{base}/{key}',"availability_topic":f'{base}/status',"payload_available":"online","payload_not_available":"offline","unique_id":f'minigeigercounter_{key}',"state_class":cls,"icon":icon,"device":{"identifiers":["minigeigercounter"],"name":"MiniGeigerCounter","manufacturer":"therepro21","model":"MiniGeigerCounter"}}
                    if unit: payload["unit_of_measurement"]=unit
                    self.mqtt.publish(f'{discovery_prefix}/sensor/minigeigercounter_{key}/config',json.dumps(payload),retain=True)
                status={"name":"Verbindung","default_entity_id":"binary_sensor.minigeigercounter_verbindung","state_topic":f'{base}/status',"payload_on":"online","payload_off":"offline","device_class":"connectivity","unique_id":"minigeigercounter_status","device":{"identifiers":["minigeigercounter"],"name":"MiniGeigerCounter","manufacturer":"therepro21","model":"MiniGeigerCounter"}}
                self.mqtt.publish(f'{discovery_prefix}/binary_sensor/minigeigercounter_status/config',json.dumps(status),retain=True)
        except Exception: pass

monitor=Monitor()
async def worker():
    last_store=last_publish=0
    while True:
        s=monitor.state()
        if time.time()-last_publish>=2: monitor.publish(s); last_publish=time.time()
        for ws in monitor.ws[:]:
            try: await ws.send_json(s)
            except Exception: monitor.ws.remove(ws)
        if time.time()-last_store>=60:
            monitor.flush_config()
            with db() as c:
                c.execute('INSERT OR REPLACE INTO samples VALUES(?,?,?,?)',(int(time.time()),s['smooth_cpm'],s['smooth_cpm']/float(monitor.cfg['counts_per_usvh']),s['total_count']))
                retention=int(monitor.cfg.get('history_retention_days',0) or 0)
                if retention>0: c.execute('DELETE FROM samples WHERE ts<?',(int(time.time()-retention*86400),))
            last_store=time.time()
        await asyncio.sleep(.1)
@asynccontextmanager
async def lifespan(app):
    monitor.restart_input(); task=asyncio.create_task(worker()); yield; task.cancel(); monitor.flush_config(); monitor.stop_input()
app=FastAPI(title='MiniGeigerCounter',version=APP_VERSION,lifespan=lifespan)
app.mount('/static',StaticFiles(directory=Path(__file__).parent/'static'),name='static')
@app.get('/')
async def home(): return FileResponse(Path(__file__).parent/'static'/'index.html')
@app.get('/api/state')
async def state(): return monitor.state()
@app.get('/api/devices')
async def devices():
    return [{"id":i,"name":d['name']} for i,d in enumerate(sd.query_devices()) if d['max_input_channels']>0]
@app.get('/api/config')
async def get_config():
    with monitor.lock:
        result=dict(monitor.cfg)
        result['mqtt_password']=''
        result['mqtt_has_password']=bool(monitor.cfg.get('mqtt_password'))
        result['update_token']=''
        result['update_has_token']=bool(monitor.cfg.get('update_token'))
        return result
@app.put('/api/config')
async def put_config(update:dict):
    if update.get('mqtt_password') in (None, ''): update.pop('mqtt_password', None)
    if update.get('update_token') in (None, ''): update.pop('update_token', None)
    allowed=set(DEFAULT)
    with monitor.lock: old=dict(monitor.cfg)
    c={**old,**{k:v for k,v in update.items() if k in allowed}}
    if 'counts_per_usvh' in update: c['cps_per_usvh']=float(c['counts_per_usvh'])/60
    elif 'cps_per_usvh' in update: c['counts_per_usvh']=float(c['cps_per_usvh'])*60
    if 'threshold' in update:
        try: c['threshold']=float(c['threshold'])
        except (TypeError, ValueError): raise HTTPException(422, 'Ungültige Impulsschwelle')
        if not 0 < c['threshold'] <= .01: raise HTTPException(422, 'Die Impulsschwelle muss zwischen 0 und 0,01 liegen')
    monitor.apply_config(c)
    if c.get('update_token') and len(c['update_token']) < 16: raise HTTPException(422, 'Der Update-Code muss mindestens 16 Zeichen haben')
    if any(old[k] != c[k] for k in ('mqtt_enabled','mqtt_host','mqtt_port','mqtt_username','mqtt_password','mqtt_topic','mqtt_discovery_prefix','home_assistant_discovery')): monitor.mqtt=None
    if any(old[k] != c[k] for k in ('counter_type','audio_device','sample_rate','gpio_pin','gpio_active_low')): monitor.restart_input()
    return {"ok":True}
def _version_key(value):
    return tuple(int(part) for part in value.strip().lstrip('v').split('.') if part.isdigit())
def _latest_version_from_github():
    """Read the lightweight version file, with the tags API as a robust fallback."""
    errors=[]
    for url in UPDATE_VERSION_URLS:
        try:
            request=urllib.request.Request(url, headers={"User-Agent":"MiniGeigerCounter-update-check/3.1"})
            with urllib.request.urlopen(request, timeout=8) as response:
                body=response.read(4096).decode('utf-8').strip()
            if url.endswith('/VERSION'):
                version=body
            else:
                tags=json.loads(body)
                versions=[str(item.get('name','')) for item in tags if str(item.get('name','')).lstrip('v').replace('.','').isdigit()]
                version=max(versions,key=_version_key) if versions else ''
            if version and all(part.isdigit() for part in version.lstrip('v').split('.')): return version
            errors.append('ungültige Antwort')
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError('; '.join(errors))
@app.get('/api/update/check')
async def update_check():
    try:
        latest=_latest_version_from_github()
        return {"current":APP_VERSION,"latest":latest,"available":_version_key(latest)>_version_key(APP_VERSION)}
    except Exception as exc:
        raise HTTPException(503, f'GitHub nicht erreichbar: {exc}')
@app.post('/api/update/install')
async def update_install(payload:dict):
    token=str(payload.get('token',''))
    with monitor.lock: configured=str(monitor.cfg.get('update_token',''))
    if not configured: raise HTTPException(409, 'Bitte zuerst einen Update-Code in den Einstellungen speichern.')
    if token != configured: raise HTTPException(403, 'Update-Code ist nicht korrekt.')
    updater=Path('/usr/local/sbin/minigeiger-update')
    if not updater.exists(): raise HTTPException(409, 'Aktualisierer fehlt. Bitte den Installer einmal manuell ausführen.')
    try:
        subprocess.Popen(['sudo','-n',str(updater)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    except Exception as exc:
        raise HTTPException(500, f'Update konnte nicht gestartet werden: {exc}')
    return {"ok":True,"message":"Update gestartet. Der Dienst wird nach der Installation automatisch neu gestartet."}
@app.get('/api/history')
async def history(hours:int=24):
    hours=max(1,min(hours,24*90)); since=int(time.time()-hours*3600); bucket=60 if hours<=24 else 300 if hours<=24*7 else 900
    with db() as c: rows=c.execute('SELECT (ts / ?) * ? AS bucket, AVG(usvh) FROM samples WHERE ts>=? GROUP BY bucket ORDER BY bucket',(bucket,bucket,since)).fetchall()
    return [{"ts":r[0],"usvh":r[1]} for r in rows]
@app.get('/api/export.pdf')
async def export_pdf(hours:int=24):
    if not REPORTLAB_AVAILABLE: raise HTTPException(503, 'PDF export requires reportlab; run the installer again')
    hours=max(1,min(hours,24*3650)); since=int(time.time()-hours*3600)
    with db() as c: rows=c.execute('SELECT ts,cpm,usvh,total FROM samples WHERE ts>=? ORDER BY ts',(since,)).fetchall()
    out=io.BytesIO(); page=canvas.Canvas(out,pagesize=A4); width,height=A4
    # Vector counterpart of the browser logo: handheld meter, pulse display, radiation waves.
    blue=colors.HexColor('#0866b6'); logo_x,logo_y=36,height-70
    page.setFillColor(blue); page.roundRect(logo_x,logo_y,25,38,5,fill=1,stroke=0)
    page.setFillColor(colors.white); page.roundRect(logo_x+4,logo_y+17,17,13,2,fill=1,stroke=0)
    page.setStrokeColor(blue); page.setLineWidth(1.5); page.line(logo_x+6,logo_y+23,logo_x+9,logo_y+23); page.line(logo_x+9,logo_y+23,logo_x+11,logo_y+27); page.line(logo_x+11,logo_y+27,logo_x+14,logo_y+20); page.line(logo_x+14,logo_y+20,logo_x+16,logo_y+24); page.line(logo_x+16,logo_y+24,logo_x+19,logo_y+24)
    page.setFillColor(colors.white); page.rect(logo_x+7,logo_y+5,11,2,fill=1,stroke=0)
    page.setStrokeColor(blue); page.setLineWidth(1.8); page.arc(logo_x+22,logo_y+11,logo_x+35,logo_y+29,300,120); page.arc(logo_x+25,logo_y+7,logo_x+42,logo_y+33,300,120)
    page.setFillColor(colors.HexColor('#102a43')); page.setFont('Helvetica-Bold',16); page.drawString(84,height-48,'MiniGeigerCounter')
    page.setFont('Helvetica',9); page.setFillColor(colors.HexColor('#526f82')); page.drawString(84,height-62,f'Bericht - letzte {hours} Stunden - erstellt {time.strftime("%d.%m.%Y %H:%M")}')
    page.setStrokeColor(colors.HexColor('#d8e4ec')); page.line(36,height-82,width-36,height-82)
    page.setFillColor(colors.HexColor('#102a43')); page.setFont('Helvetica-Bold',11); page.drawString(36,height-108,'Messverlauf')
    y=height-130; page.setFont('Helvetica-Bold',8)
    for x,label in ((36,'Zeit'),(175,'CPM'),(270,'uSv/h'),(370,'Impulse gesamt')): page.drawString(x,y,label)
    page.setFont('Helvetica',8); y-=13
    step=max(1,len(rows)//42)
    for ts,cpm,usvh,total in rows[::step]:
        if y<65: page.showPage(); y=height-55; page.setFont('Helvetica',8)
        page.setFillColor(colors.HexColor('#102a43')); page.drawString(36,y,time.strftime('%d.%m.%Y %H:%M',time.localtime(ts)))
        page.drawRightString(235,y,f'{cpm:.2f}'.replace('.',',')); page.drawRightString(340,y,f'{usvh:.4f}'.replace('.',',')); page.drawRightString(470,y,str(total)); y-=12
    page.setStrokeColor(colors.HexColor('#d8e4ec')); page.line(36,42,width-36,42); page.setFillColor(colors.HexColor('#526f82')); page.setFont('Helvetica',8); page.drawString(36,28,f'Copyright by Michael P. Thiess - MiniGeigerCounter v{APP_VERSION}'); page.drawRightString(width-36,28,'github.com/therepro21/MiniGeigerCounter')
    page.save(); return Response(content=out.getvalue(),media_type='application/pdf',headers={'Content-Disposition':f'attachment; filename="MiniGeigerCounter_{hours}h.pdf"'})
@app.delete('/api/history')
async def delete_history(hours:int=0):
    """Delete recorded aggregates from the last N hours; 0 clears all history."""
    if hours<0: raise HTTPException(422, 'hours must not be negative')
    with db() as c:
        if hours==0: c.execute('DELETE FROM samples')
        else: c.execute('DELETE FROM samples WHERE ts>=?',(int(time.time()-hours*3600),))
        c.commit()
        c.execute('VACUUM')
    return {"ok":True,"database_bytes":DB_FILE.stat().st_size if DB_FILE.exists() else 0}
@app.websocket('/ws')
async def websocket(ws:WebSocket):
    await ws.accept(); monitor.ws.append(ws)
    try:
        while True: await ws.receive_text()
    except Exception:
        if ws in monitor.ws: monitor.ws.remove(ws)
if __name__=='__main__': uvicorn.run(app,host='0.0.0.0',port=int(config()['web_port']))
