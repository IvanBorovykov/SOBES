"""Check model access for every saved profile without persisting credentials."""
import sys,json,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from api_keys import KeyPool,KeychainStore
from file_context import FileContext
from google.genai import types
from benchmarks.files_project.voice_qa import FILES,ROOT,read_audio,evaluate
from audio_payload import prepare_audio
keys=KeyPool(KeychainStore());context,_=FileContext().load([str(ROOT/file) for file in FILES]);payload=prepare_audio(read_audio('locate','clean'))
try:
 for index,entry in enumerate(keys.entries()[1:],1):
  start=time.monotonic();text='';first=None
  try:
   stream=keys.client(entry).models.generate_content_stream(model='gemini-3.5-flash-lite',
     contents=[types.Part.from_bytes(data=payload.data,mime_type=payload.mime_type),'Відповідай коротко за файлами: де функція з голосового питання? Цитуй [F2, рядок N]. DOCUMENTS_JSON:'+context],
     config=types.GenerateContentConfig(thinking_config=types.ThinkingConfig(thinking_level='high'),http_options=types.HttpOptions(timeout=20000)))
   try:
    for chunk in stream:
     if chunk.text:first=first or time.monotonic();text+=chunk.text
   finally:stream.close()
   row=dict(profile=index,model='gemini-3.5-flash-lite',status='ok',first_text_s=first-start if first else None,total_s=time.monotonic()-start,text=text,**evaluate('locate',text))
  except Exception as e:row=dict(profile=index,status='error',error=keys.redact(e)[:1200])
  with (ROOT/'profile_generation.jsonl').open('a') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n')
  print(json.dumps({k:v for k,v in row.items() if k!='text'},ensure_ascii=False),flush=True)
finally:keys.close()
