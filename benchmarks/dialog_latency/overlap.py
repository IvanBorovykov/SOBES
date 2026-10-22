"""Continue feeding a follow-up while the previous real API answer is generating."""
import json,sys,time,threading,wave
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import assistant
from api_keys import KeyPool,KeychainStore
from benchmarks.option_q_probe import Audio,Overlay
ROOT=Path(__file__).resolve().parent

def audio(case):
 with wave.open(str(ROOT/(case+'.wav'))) as f:return f.readframes(f.getnframes())

def feed(source,pcm):
 start=time.monotonic()
 for off in range(0,len(pcm),640):
  source.callback(pcm[off:off+640].ljust(640,b'\x00'))
  time.sleep(max(0,start+min(off+640,len(pcm))/32000-time.monotonic()))

def request(app,source,overlay):
 app._generation+=1;started=time.monotonic();offset=len(overlay.events)
 worker=threading.Thread(target=app._run_live,args=(app._generation,time.perf_counter(),False),kwargs={'audio_only':True},daemon=True)
 worker.start();return worker,started,offset

keys=KeyPool(KeychainStore());source=Audio();source._seconds=30;source.clear=lambda:setattr(source,'pcm',b'');overlay=Overlay();overlay.hide=lambda:None;app=assistant.Assistant(overlay,source,keys)
rows=[]
try:
 with patch.object(assistant,'TRANSCRIPT_MODE',True),patch.object(assistant.AppHelper,'callAfter',side_effect=lambda fn,*args:fn(*args)):
  app.start_realtime();end=time.monotonic()+10
  while not app._realtime._channel.hint.ready and time.monotonic()<end:time.sleep(.01)
  source.pcm=audio('numbers');feed(source,source.pcm)
  worker,pressed,offset=request(app,source,overlay)
  time.sleep(.05)
  source.pcm=audio('numbers_follow');overlapped=worker.is_alive();feed(source,source.pcm)
  worker.join(12)
  events=overlay.events[offset:];final=events[-1][1] if events else ''
  first=[t for t,text in events if 'перший текст' in text]
  rows.append({'case':'continued_listening','next_audio_started_while_previous_worker_active':overlapped,'first_s':first[0]-pressed if first else None,'complete':'✓ Завершено' in final,'text':final})
  worker,pressed,offset=request(app,source,overlay)
  while worker.is_alive() and time.monotonic()-pressed<15:source.callback(bytes(640));time.sleep(.02)
  events=overlay.events[offset:];final=events[-1][1] if events else ''
  first=[t for t,text in events if 'перший текст' in text]
  rows.append({'case':'followup_captured_during_previous_answer','first_s':first[0]-pressed if first else None,'complete':'✓ Завершено' in final,'text':final})
  app.clear()
  assert not app._dialog
finally:
 (ROOT/'overlap.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
 app.close()
(ROOT/'overlap.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
for row in rows:print({k:v for k,v in row.items() if k!='text'},flush=True)
