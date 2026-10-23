#!/usr/bin/env python3
"""Compatibility launcher for the standard streaming-STT and Flash-Lite dialog."""
import os

if __name__ == '__main__':
    os.environ['SOBES_AUDIO_PIPELINE'] = 'transcript'
    from assistant import main
    main()
