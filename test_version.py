#!/usr/bin/env python3
"""Run experimental STT -> Flash-Lite Q; normal assistant.py remains selectable."""
import os

if __name__ == '__main__':
    os.environ['SOBES_AUDIO_PIPELINE'] = 'transcript'
    from assistant import main
    main()
