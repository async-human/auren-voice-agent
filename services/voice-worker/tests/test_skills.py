from __future__ import annotations

import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from skills import MAX_SKILL_CHARS, load_agent_skills


class AgentSkillTests(unittest.TestCase):
    def test_default_client_followup_skill_is_loaded(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AGENT_SKILLS", None)
            os.environ.pop("AGENT_SKILLS_DIR", None)
            content = load_agent_skills()

        self.assertIn("Client follow-up operator", content)
        self.assertIn("sending always uses the gateway approval flow", content)

    def test_skill_names_cannot_escape_the_configured_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(
                os.environ,
                {"AGENT_SKILLS": "../../secret", "AGENT_SKILLS_DIR": directory},
            ):
                self.assertEqual(load_agent_skills(), "")

    def test_loaded_skill_content_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "large.md").write_text("x" * (MAX_SKILL_CHARS + 100))
            with patch.dict(
                os.environ,
                {"AGENT_SKILLS": "large", "AGENT_SKILLS_DIR": directory},
            ):
                content = load_agent_skills()

        self.assertLessEqual(len(content), MAX_SKILL_CHARS + 40)


if __name__ == "__main__":
    unittest.main()
