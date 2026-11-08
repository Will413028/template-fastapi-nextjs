"""Step 1 (f), second half: writes spec pairs for `oasdiff breaking`, from the spike app's spec, into the directory
given. Each pair differs in one thing: a value added to Problem.code's x-extensible-enum, the same with a plain
`enum` (control), and a required response field removed (control that the tool reports an ERR at all).

    backend/.venv/bin/python docs/plans/2026-11-08-fastapi-code-first/spike/make_enum_specs.py <dir>
"""

import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import proto

out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)
base = proto.build(proto.OrderedRoute, fix_problem_media_type=True).openapi()


def code(spec: dict) -> dict:
    return spec["components"]["schemas"]["Problem"]["properties"]["code"]


def write(name: str, spec: dict) -> None:
    (out / f"{name}.json").write_text(json.dumps(spec, indent=2) + "\n")


ext_added = copy.deepcopy(base)
code(ext_added)["x-extensible-enum"].append("new_code")

enum_base = copy.deepcopy(base)
code(enum_base)["enum"] = code(enum_base).pop("x-extensible-enum")
enum_added = copy.deepcopy(enum_base)
code(enum_added)["enum"].append("new_code")

field_removed = copy.deepcopy(base)
item = field_removed["components"]["schemas"]["Item"]
item["properties"].pop("title")
item["required"].remove("title")

write("base", base)
write("ext-added", ext_added)
write("enum-base", enum_base)
write("enum-added", enum_added)
write("field-removed", field_removed)
