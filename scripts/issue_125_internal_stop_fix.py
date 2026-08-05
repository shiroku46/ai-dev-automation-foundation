#!/usr/bin/env python3
"""Restore the non-notifying internal-stop boundary in the Bootstrap checklist."""
from pathlib import Path

path = Path(__file__).resolve().parents[1] / "bootstrap/generator.py"
text = path.read_text(encoding="utf-8")
old = "- [ ] Optional provider failure remains non-blocking with `human_action_required: false`.\n- [ ] Never output, persist, copy, hash, or infer Secret values."
new = "- [ ] Optional provider failure remains non-blocking with `human_action_required: false`.\n- [ ] Persist routine automation stops only on `automation-internal-stops`; never publish routine stop comments.\n- [ ] Never output, persist, copy, hash, or infer Secret values."
if text.count(old) != 1:
    raise SystemExit("internal-stop checklist insertion point changed")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
