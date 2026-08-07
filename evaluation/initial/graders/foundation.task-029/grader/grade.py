from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

KEYS = (
    "AI_DEV_EVAL_TASK_ID","AI_DEV_EVAL_TASK_VERSION","AI_DEV_EVAL_MANIFEST_SHA256","AI_DEV_EVAL_GRADER_SHA256","AI_DEV_EVAL_FOUNDATION_SHA","AI_DEV_EVAL_BASE_SHA","AI_DEV_EVAL_CANDIDATE_SHA",
)

def arguments():
    if len(sys.argv) != 5 or sys.argv[1] != "--workspace" or sys.argv[3] != "--result": raise SystemExit(2)
    return Path(sys.argv[2]), Path(sys.argv[4])

def identity():
    value = {key: os.environ[key] for key in KEYS}
    return {"task_id":value[KEYS[0]],"task_version":int(value[KEYS[1]]),"manifest_sha256":value[KEYS[2]],"grader_sha256":value[KEYS[3]],"foundation_sha":value[KEYS[4]],"base_sha":value[KEYS[5]],"candidate_sha":value[KEYS[6]]}

def finish(path, passed):
    check={"check_id":"actions_permission","outcome":"passed" if passed else "failed","message":"Synthetic Actions permission is read-only." if passed else "Synthetic Actions permission remains overbroad.","evidence_paths":[".github/workflows/actions.yml"]}
    outcome="passed" if passed else "failed"
    raw=json.dumps({"schema_version":1,**identity(),"outcome":outcome,"checks":[check],"summary":"Protected Actions-boundary acceptance passed." if passed else "Protected Actions-boundary acceptance failed."},sort_keys=True,separators=(",",":"),ensure_ascii=False).encode("utf-8")
    temporary=path.with_name(path.name+".tmp"); temporary.write_bytes(raw); os.replace(temporary,path); return 0 if passed else 1

workspace,result=arguments()
try:
    source=(workspace/".github/workflows/actions.yml").read_text(encoding="utf-8"); normalized="".join(source.split()); passed="actions:read" in normalized and "actions:write" not in normalized
except (OSError,UnicodeError): passed=False
raise SystemExit(finish(result,passed))
