import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from kitty_tab_monitor.llm import _extract_json, _normalize_decision, decide


class ExtractJsonTests(unittest.TestCase):
    def test_extracts_fenced_json(self):
        self.assertEqual(_extract_json('```json\n{"action":"none"}\n```'), {"action": "none"})


class PromptTests(unittest.TestCase):
    @patch("kitty_tab_monitor.llm._post_stream")
    def test_includes_workspace_without_recursively_expanding_screen_text(self, post_stream):
        response = {
            "is_waiting": False,
            "action": "none",
            "text_to_send": "",
            "press_enter": False,
            "confidence": 0.9,
            "reason": "No prompt",
        }
        post_stream.return_value = (json.dumps(response), None)
        config = SimpleNamespace(
            openai_api_key="test-key",
            openai_base_url="https://example.test/v1",
            model="test-model",
            system_prompt="",
            user_prompt_template="",
        )

        decision, error = decide(
            config, "agent", "literal {workspace}", "/repo", "/repo/src"
        )

        self.assertIsNone(error)
        self.assertEqual(decision["action"], "none")
        request = post_stream.call_args.args[1]
        system_prompt = request["messages"][0]["content"]
        user_prompt = request["messages"][1]["content"]
        self.assertIn("PIDs greater than or equal to 1000 is safe", system_prompt)
        self.assertIn("any PID below 1000", system_prompt)
        self.assertIn("local Docker test database", system_prompt)
        self.assertIn("production, remote, shared", system_prompt)
        self.assertIn("~/.claude/jobs/*/tmp/*", system_prompt)
        self.assertIn("exact matching job tmp directory", system_prompt)
        self.assertIn("Broader access to ~/.claude is unsafe", system_prompt)
        self.assertIn("choose the persistent approval", system_prompt)
        self.assertIn("fall back to the one-time approval", system_prompt)
        self.assertIn("when neither approval is safe", system_prompt)
        self.assertIn("concise 2-8 word rationale", system_prompt)
        self.assertNotIn('"context"', system_prompt)
        self.assertIn("Workspace: /repo", user_prompt)
        self.assertIn("Current directory: /repo/src", user_prompt)
        self.assertIn("literal {workspace}", user_prompt)


class NormalizeDecisionTests(unittest.TestCase):
    def test_accepts_safe_type_action(self):
        decision, error = _normalize_decision({
            "is_waiting": True,
            "action": "type",
            "text_to_send": "2",
            "press_enter": True,
            "confidence": "0.91",
            "reason": "Cancel is\n the safe choice",
        })

        self.assertIsNone(error)
        self.assertEqual(decision["text_to_send"], "2")
        self.assertEqual(decision["confidence"], 0.91)
        self.assertEqual(decision["reason"], "Cancel is the safe choice")
        self.assertNotIn("context", decision)

    def test_rejects_type_action_when_not_waiting(self):
        decision, error = _normalize_decision({
            "is_waiting": False,
            "action": "type",
            "text_to_send": "y",
            "press_enter": True,
            "confidence": 0.9,
            "reason": "",
        })

        self.assertIsNone(decision)
        self.assertIn("without a waiting prompt", error)

    def test_rejects_non_string_payload(self):
        decision, error = _normalize_decision({
            "is_waiting": True,
            "action": "type",
            "text_to_send": ["y"],
            "press_enter": True,
            "confidence": 0.9,
            "reason": "",
        })

        self.assertIsNone(decision)
        self.assertIn("text_to_send", error)


if __name__ == "__main__":
    unittest.main()
