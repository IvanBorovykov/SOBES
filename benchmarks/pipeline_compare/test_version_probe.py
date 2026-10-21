"""Exercise the experimental app's real STT and answer path, synthetic audio."""
import sys,time,threading,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import assistant
from api_keys import KeyPool,KeychainStore
from benchmarks.option_q_probe import Audio,Overlay
from benchmarks.pipeline_compare.compare import samples

keys=KeyPool(KeychainStore());source=Audio();source._seconds=8;overlay=Overlay()
app=assistant.Assistant(overlay,source,keys)
try:
 with patch.object(assistant,'TRANSCRIPT_MODE',True),patch.object(assistant.AppHelper,'callAfter',side_effect=lambda fn,*args:fn(*args)):
  app.start_realtime();deadline=time.monotonic()+10
  while not app._realtime._channel.hint.ready and time.monotonic()<deadline:time.sleep(.02)
  assert app._realtime._channel.hint.ready,app._realtime.status()
  for case,variant in [('python','clean'),('last_question','clean'),('sql','noise'),('async','quiet')]:
   pcm=samples(case,variant);source.pcm=pcm[-8*32000:];overlay.events=[];start=time.monotonic()
   for off in range(0,len(pcm),640):
    source.callback(pcm[off:off+640].ljust(640,b'\x00'))
    time.sleep(max(0,start+min(off+640,len(pcm))/32000-time.monotonic()))
   pressed=time.monotonic();worker=threading.Thread(target=app._run_live,args=(app._generation,time.perf_counter(),False),kwargs={'audio_only':True},daemon=True);worker.start()
   while worker.is_alive() and time.monotonic()-pressed<15:
    source.callback(bytes(640));time.sleep(.02)
   if worker.is_alive():app._generation+=1;worker.join(1)
   final=overlay.events[-1][1] if overlay.events else ''
   first=[t for t,text in overlay.events if 'перший текст' in text]
   row={'case':case,'variant':variant,'first_text_s':first[0]-pressed if first else None,'total_s':time.monotonic()-pressed,
        'used_transcript':'Питання:' in final,'complete':'✓ Завершено' in final,'text':final}
   with Path('benchmarks/pipeline_compare/test_version.jsonl').open('a') as file:file.write(json.dumps(row,ensure_ascii=False)+'\n')
   print({k:v for k,v in row.items() if k!='text'},flush=True)
finally:app.close()
