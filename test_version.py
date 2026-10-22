#!/usr/bin/env python3
"""Beta2: streaming STT, short dialog history and multiple-key Flash-Lite Q."""
import os

if __name__ == '__main__':
    os.environ['SOBES_AUDIO_PIPELINE'] = 'transcript'
    from assistant import main
    main()
