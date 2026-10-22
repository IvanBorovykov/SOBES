"""Real paired text-only comparison of one key vs delayed two-key race."""
from pathlib import Path
import sys,time,json
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from api_keys import KeyPool,KeychainStore
from answer_stream import first_answer
from google.genai import types
from benchmarks.dialog_latency.probe import TURNS
keys=KeyPool(KeychainStore())
try:
 for i in range(8):
  for mode in (['single','race'] if i%2==0 else ['race','single']):
   question=TURNS[i][1];t=time.monotonic();first=None;text='';stats={}
   # Fixed primary rotated by trial; race shares same primary as paired control.
   entries=keys.entries();after=entries[(i-1)%len(entries)].id
   try:
    for c in first_answer(keys,'gemini-3.5-flash-lite',question,types.GenerateContentConfig(
      thinking_config=types.ThinkingConfig(thinking_level='minimal'),
      system_instruction='Ответь кратко на вопрос.',http_options=types.HttpOptions(timeout=10000)),
      lambda:False,after_id=after,hedge_seconds=.18 if mode=='race' else 100,metrics=stats):
     first=first or time.monotonic();text+=c.text
    row={'trial':i,'mode':mode,'first_s':first-t,'total_s':time.monotonic()-t,'stats':stats,'text':text}
   except Exception as e:row={'trial':i,'mode':mode,'status':'error','error':keys.redact(e)[:500],'stats':stats}
   with Path('benchmarks/dialog_latency/key_race.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
   print({k:v for k,v in row.items() if k!='text'},flush=True)
finally:keys.close()
