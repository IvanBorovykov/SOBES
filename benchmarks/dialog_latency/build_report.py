"""Recompute descriptive results; manual quality review includes source hashes."""
import hashlib,json,statistics
from pathlib import Path
ROOT=Path(__file__).resolve().parent
review=json.loads((ROOT/'review.json').read_text())
summary={}
for name in ['baseline','dialog_batched_race','speculative','context_single_100ms','context_single_20ms','final','native','overlap']:
 path=ROOT/(name+'.jsonl');rows=[json.loads(line) for line in path.read_text().splitlines()]
 times=[r.get('first_text_s',r.get('first_s')) for r in rows]
 summary[name]={'n':len(rows),'median_first_s':statistics.median(times),'range_first_s':[min(times),max(times)],'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
for name in ['baseline','final','native','overlap']:
 section=review['series'][name]
 assert section['sha256']==summary[name]['sha256']
 assert section['n']==summary[name]['n']
 summary[name]['correct']=section['correct']
final=[json.loads(line) for line in (ROOT/'final.jsonl').read_text().splitlines()]
summary['final']['median_stt_s']=statistics.median(r['metrics']['stt_s'] for r in final)
summary['final']['median_llm_s']=statistics.median(r['metrics']['text_llm_s'] for r in final)
summary['final']['text_attempts']=sum(r['metrics']['attempts'] for r in final)
summary['final']['winner_profiles']=[r['metrics']['winner_profile'] for r in final]
summary['final']['under_one_second']=sum(r['first_text_s']<1 for r in final)
(ROOT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(summary,ensure_ascii=False,indent=2))
