"""Dump tool-state types + the event payload shapes."""
import pathlib
import re

base = pathlib.Path("node_modules/@opencode/client/dist/promise/generated")
text = (base / "types.d.ts").read_text(encoding="utf-8", errors="replace")

# tool states
for name in re.findall(r"SessionToolState\w+", text[:200000]):
    pass
names = sorted(set(re.findall(r"SessionToolState\w+", text)))
for name in names[:8]:
    match = re.search(
        rf"^(export type {name}\b.*?)(?=^export )", text, re.M | re.S
    )
    if match:
        print(match.group(1)[:700])
        print("~" * 50)

# one event payload shape so we can switch on type
for name in ["SessionTextDelta", "SessionToolCalled", "SessionStepStarted", "SessionCreated", "SessionExecutionStarted", "SessionUsageUpdated"]:
    match = re.search(
        rf"^(export type {name}\b.*?)(?=^export )", text, re.M | re.S
    )
    if match:
        print(match.group(1)[:900])
        print("~" * 50)
