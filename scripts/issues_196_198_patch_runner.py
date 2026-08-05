#!/usr/bin/env python3
"""Execute the deterministic patch generator without replacement escape expansion."""
from __future__ import annotations

from pathlib import Path

path = Path(__file__).with_name("issues_196_198_patch.py")
source = path.read_text(encoding="utf-8")
if source.count("implement = '''\n") != 1:
    raise SystemExit("implement literal identity changed")
method_literals = source.count("'''    def ")
if method_literals != 8:
    raise SystemExit(f"expected eight method literals, found {method_literals}")
old_subn = '    updated, count = pattern.subn(replacement.rstrip() + "\\n\\n", text, count=1)'
new_subn = (
    '    value = replacement.rstrip() + "\\n\\n"\n'
    '    updated, count = pattern.subn(lambda _match: value, text, count=1)'
)
if source.count(old_subn) != 1:
    raise SystemExit("replace_method substitution identity changed")
source = source.replace(old_subn, new_subn, 1)
source = source.replace("implement = '''\n", "implement = r'''\n", 1)
source = source.replace("'''    def ", "r'''    def ")
code = compile(source, str(path), "exec")
namespace = {"__name__": "__main__", "__file__": str(path)}
exec(code, namespace)
