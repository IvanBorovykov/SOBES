"""Generate the exact synthetic benchmark questions on macOS (no microphone)."""
import hashlib
import json
import subprocess
import tempfile
import wave
from pathlib import Path
from voice_models import CASES,ROOT
records={}
with tempfile.TemporaryDirectory(prefix='sobes-synthetic-') as directory:
    for case,(text,_) in CASES.items():
        aiff=Path(directory)/f'{case}.aiff'
        target=ROOT/f'{case}.wav'
        subprocess.run(['say','-v','Milena','-o',str(aiff),text],check=True)
        subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-y','-i',str(aiff),
                        '-ar','16000','-ac','1',str(target)],check=True)
        with wave.open(str(target)) as wav:
            records[case]={'text':text,'voice':'macOS Milena','sample_rate':wav.getframerate(),
                           'channels':wav.getnchannels(),'sample_width':wav.getsampwidth(),
                           'seconds':wav.getnframes()/wav.getframerate(),
                           'sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
(ROOT/'audio_manifest.json').write_text(json.dumps(records,ensure_ascii=False,indent=2))
