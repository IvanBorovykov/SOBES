"""Real ask_files() worker and production model/config with altered synthetic voices."""
import json,sys,time,threading
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import assistant
from api_keys import KeyPool,KeychainStore
from benchmarks.files_project.voice_qa import ROOT,FILES,Audio,Overlay,read_audio,evaluate

keys=KeyPool(KeychainStore());overlay=Overlay();source=Audio(b'');app=assistant.Assistant(overlay,source,keys)
app.context_files=tuple(str(ROOT/file) for file in FILES)
try:
 with patch.object(assistant.AppHelper,'callAfter',side_effect=lambda fn,*a:fn(*a)),patch.object(assistant,'capture_screenshot',side_effect=AssertionError('No screenshot')):
  for variant,case in [('quiet','locate'),('quiet','callers'),('quiet','calculation'),('noise','locate'),('noise','rollback'),('noise','calculation'),('noise','cancel')]:
   source.pcm=read_audio(case,variant);overlay.events=[];started=time.monotonic()
   # Wait on the exact thread launched by ask_files without replacing its execution.
   original=threading.Thread;created=[]
   def worker(*args,**kwargs):
    thread=original(*args,**kwargs);created.append(thread);return thread
   with patch.object(assistant.threading,'Thread',side_effect=worker):app.ask_files()
   for thread in created:thread.join(timeout=130)
   final=overlay.events[-1][1] if overlay.events else ''
   shown=[(t,text) for t,text in overlay.events if 'перший текст' in text]
   row=dict(case=case,variant=variant,model=final.split(' · ')[0],intended_primary=assistant.FILES_PRIMARY_MODEL,production_config=True,
     status='ok' if '✓ Завершено' in final else 'error',first_text_s=shown[0][0]-started if shown else None,
     total_s=time.monotonic()-started,text=final,**evaluate(case,final))
   with (ROOT/'production_high.jsonl').open('a') as target:target.write(json.dumps(row,ensure_ascii=False)+'\n')
   print(json.dumps({k:v for k,v in row.items() if k!='text'},ensure_ascii=False),flush=True)
   time.sleep(max(0,15-(time.monotonic()-started)))
finally:app.close()
