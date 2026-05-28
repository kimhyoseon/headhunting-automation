from __future__ import annotations

import re


def redact_candidate_name(text: str, name: str) -> str:
    text = str(text or "")
    name = str(name or "").strip()
    if not text or not name or name == "-":
        return text

    variants = {name}
    compact_name = re.sub(r"\s+", "", name)
    if compact_name:
        variants.add(compact_name)

    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line
        if any(line.strip() == value for value in variants):
            continue
        for value in sorted(variants, key=len, reverse=True):
            if value:
                line = line.replace(value, "")
        line = re.sub(r"[ \t]{2,}", " ", line).strip()
        if line:
            lines.append(line)
    return "\n".join(lines).strip()
