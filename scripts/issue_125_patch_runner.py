#!/usr/bin/env python3
"""Execute the deterministic Issue #125 patch with normalized EOF handling."""
from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("issue_125_patch.py")
source = path.read_text(encoding="utf-8")
old = 'startup_path.write_text(startup.rstrip() + section + "\\n", encoding="utf-8")'
new = 'startup_path.write_text(startup.rstrip() + section.rstrip() + "\\n", encoding="utf-8")'
if source.count(old) != 1:
    raise SystemExit("startup EOF patch identity changed")
source = source.replace(old, new, 1)
code = compile(source, str(path), "exec")
exec(code, {"__name__": "__main__", "__file__": str(path)})
