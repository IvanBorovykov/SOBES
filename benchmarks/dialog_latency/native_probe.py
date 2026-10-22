"""Generated audio playback -> native CoreAudio tap -> real experimental Q path."""
import array,json,sys,time,subprocess,threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import assistant
from api_keys import KeyPool,KeychainStore
from benchmarks.option_q_probe import Overlay
ROOT=Path(__file__).resolve().parent
keys=KeyPool(KeychainStore());ring=assistant.AudioRing('system');ring.set_seconds(30)
overlay=Overlay();app=assistant.Assistant(overlay,ring,keys);rows=[]
try:
 with patch.object(assistant,'TRANSCRIPT_MODE',True),patch.object(assistant.AppHelper,'callAfter',side_effect=lambda fn,*args:fn(*args)):
  app.start_realtime();deadline=time.monotonic()+10
  while (ring.error or not app._realtime._channel.hint.ready) and time.monotonic()<deadline:time.sleep(.02)
  assert not ring.error,ring.error
  assert app._realtime._channel.hint.ready,app._realtime.status()
  for case in ['numbers','numbers_follow','sql']:
   overlay.events=[]
   subprocess.run(['afplay',str(ROOT/(case+'.wav'))],check=True)
   time.sleep(.1)
   pcm=ring.pcm_bytes();peak=max(map(abs,array.array('h',pcm)),default=0)
   app._generation+=1;pressed=time.monotonic()
   worker=threading.Thread(target=app._run_live,args=(app._generation,time.perf_counter(),False),kwargs={'audio_only':True},daemon=True)
   worker.start();worker.join(15)
   if worker.is_alive():app._generation+=1;worker.join(1)
   first=[t for t,txt in overlay.events if 'перший текст' in txt];final=overlay.events[-1][1] if overlay.events else ''
   row={'case':case,'first_s':first[0]-pressed if first else None,'total_s':time.monotonic()-pressed,'peak_pcm':peak,
    'complete':'✓ Завершено' in final,'text':final,'metrics':dict(app.last_audio_metrics)}
   rows.append(row);print({k:v for k,v in row.items() if k!='text'},flush=True)
finally:ring.close();app.close()
(ROOT/'native.jsonl').write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in rows))
