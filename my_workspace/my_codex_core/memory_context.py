from __future__ import annotations

import re
from pathlib import Path


def memory_document_has_user_values(content: str) -> bool:
    for raw_line in str(content or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if re.fullmatch(r"[-*]\s*[^:：]+[:：]\s*", line):
            continue
        return True
    return False


def load_long_term_memory_context(memory_root: Path) -> str:
    if not memory_root.exists():
        return ""

    sections: list[str] = []
    for path in sorted(memory_root.glob("*.md")):
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        if content and memory_document_has_user_values(content):
            sections.append(f"### {path.name}\n{content}")
    return "\n\n".join(sections)
