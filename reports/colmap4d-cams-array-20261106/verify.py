"""Compatibility entry point for the current timed-camera verification."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parents[1] /
                  'colmap4d-cams-timed-contract-20261106/verify.py'), run_name='__main__')
