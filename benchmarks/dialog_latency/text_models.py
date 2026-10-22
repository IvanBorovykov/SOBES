"""Fresh text-only candidates with identical compact dialog; real API calls."""
from pathlib import Path
import sys,time,json
from contextlib import closing
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from api_keys import KeyPool,KeychainStore,credential_error
from google.genai import types
from assistant import DIALOG_INSTRUCTION
MODELS=['gemini-3.5-flash-lite','gemini-2.5-flash-lite','gemini-3.1-flash-lite','gemma-4-26b-a4b-it','gemma-4-31b-it','gemini-omni-flash-preview']
keys=KeyPool(KeychainStore())
try:
 for trial in range(2):
  for model in MODELS:
   question=('В списке 7,11,19. Добавили23. Какая сумма?' if trial==0 else 'Чем заменить time.sleep внутри async def, чтобы не блокировать event loop?')
   t=time.monotonic();first=None;text='';profile=None
   for e in keys.candidates(model):
    try:
     thinking=types.ThinkingConfig(thinking_budget=0) if '2.5' in model else types.ThinkingConfig(thinking_level='minimal') if 'flash-lite' in model else None
     config=types.GenerateContentConfig(thinking_config=thinking,http_options=types.HttpOptions(timeout=15000,retry_options=types.HttpRetryOptions(attempts=1)))
     with closing(keys.client(e).models.generate_content_stream(model=model,contents=DIALOG_INSTRUCTION+'\n'+question,config=config)) as stream:
      for c in stream:
       if c.text:first=first or time.monotonic();text+=c.text
     profile=keys.entries().index(e);break
    except Exception as err:
     if credential_error(err):keys.failed(e,model,err);continue
     row={'trial':trial,'model':model,'status':'error','error':keys.redact(err)[:500]};break
   else:row={'trial':trial,'model':model,'status':'unavailable'}
   if profile is not None:row={'trial':trial,'model':model,'status':'ok','profile':profile,'first_s':first-t if first else None,'total_s':time.monotonic()-t,'text':text}
   with Path('benchmarks/dialog_latency/text_models.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
   print({k:v for k,v in row.items() if k!='text'},flush=True)
finally:keys.close()
