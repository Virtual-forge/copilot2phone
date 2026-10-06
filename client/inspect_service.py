"""Find the headers function in the service module source."""
import pathlib
import re

root = pathlib.Path("node_modules/@opencode/client/dist")
for candidate in root.rglob("*.js"):
    text = candidate.read_text(encoding="utf-8", errors="replace")
    if "service" in candidate.name.lower() or "headers" in text[:2000]:
        # find the headers function
        for match in re.finditer(r"(?:function\s+headers|headers\s*=|headers2)", text):
            start = max(0, match.start() - 200)
            snippet = text[start : match.end() + 600]
            if "password" in snippet.lower() or "authoriz" in snippet.lower():
                print(f"== {candidate.name}")
                print(snippet[:800])
                print("~" * 50)
                break
