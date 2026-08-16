"""Load bounded, auditable workflow playbooks for the voice agent."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

logger = logging.getLogger("auren.skills")

DEFAULT_SKILLS_DIR = Path(__file__).with_name("skill_specs")
MAX_SKILL_CHARS = 12_000
_SAFE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


def load_agent_skills() -> str:
    names = [
        item.strip().lower()
        for item in os.getenv("AGENT_SKILLS", "client_followup").split(",")
        if item.strip()
    ]
    directory = Path(os.getenv("AGENT_SKILLS_DIR", str(DEFAULT_SKILLS_DIR)))
    blocks: list[str] = []
    remaining = MAX_SKILL_CHARS
    for name in names:
        if not _SAFE_NAME.fullmatch(name):
            logger.warning("Ignoring invalid agent skill name %r", name)
            continue
        path = directory / f"{name}.md"
        try:
            content = path.read_text(encoding="utf-8").strip()
        except OSError as error:
            logger.warning("Could not load agent skill %s: %s", name, error)
            continue
        if not content or remaining <= 0:
            continue
        content = content[:remaining]
        blocks.append(f"Workflow playbook: {name}\n{content}")
        remaining -= len(content)
    return "\n\n".join(blocks)
