#!/usr/bin/env python3
"""Execute the deterministic patch generator with raw replacement literals."""
from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("issues_196_198_patch.py")
source = path.read_text(encoding="utf-8")
source = source.replace("implement = '''\n", "implement = r'''\n", 1)
source = source.replace("\n    '''    def ", "\n    r'''    def ")
compile(source, str(path), "exec")
namespace = {"__name__": "__main__", "__file__": str(path)}
exec(compile(source, str(path), "exec"), namespace)
