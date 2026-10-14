#!/usr/bin/env python3
"""Compatibility entry point for the sentence-first pipeline (dry-run by default).

The old duration/word-count rebalance implementation is archived under
archive/qa/legacy. No script from the historical handoff is executed here.
"""
from pathlib import Path
import runpy

if __name__ == '__main__':
    runpy.run_path(str(Path(__file__).resolve().parent / 'subtitle_pipeline' / 'semantic_resegment.py'),
                   run_name='__main__')
