import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import httpx
from django.core.management import call_command
from django.test import TestCase

from app.management.commands.eval_recommendations import (
    CASES,
    PROFILES,
    Case,
    cost_for,
    style_flags,
)
from app.recommendations import RecommendationError, ask_provider

DISCOVERY = [
    {
        "title": "New game 0",
        "rationale": "Its puzzle design suits your taste.",
        "drawback": "the slow start before systems open up",
    },
    {
        "title": "New game 1",
        "rationale": "It builds on what worked in Portal 2.",
        "drawback": "a thin story",
    },
    {
        "title": "New game 2",
        "rationale": "Short sessions fit how you play.",
        "drawback": "sparse content after the midpoint",
    },
    {
        "title": "New game 3",
        "rationale": "It rewards the experimenting you enjoy.",
        "drawback": "clunky menus",
    },
    {
        "title": "New game 4",
        "rationale": "The pacing matches your Factorio sessions.",
        "drawback": "a steep difficulty spike late",
    },
]

RUN_PICKS = [pick | {"category": "discover"} for pick in DISCOVERY[:3]]


def provider_body(content: dict, usage: dict | None = None) -> dict:
    body: dict = {
        "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(content)}}
        ]
    }
    if usage is not None:
        body["usage"] = usage
    return body


class StyleFlagsTests(TestCase):
    def test_flags_prompt_violations(self) -> None:
        picks = [
            {
                "title": "A",
                "category": "discover",
                "rationale": "Stunning visuals carry A, you said it yourself!",
                "drawback": "may not suit everyone",
            },
            {
                "title": "B",
                "category": "discover",
                "rationale": "The same four words repeated here",
                "drawback": "grindy fights",
            },
            {
                "title": "C",
                "category": "discover",
                "rationale": "The same four words repeated here",
                "drawback": "a short campaign",
            },
        ]
        picks[0]["rationale"] += " A – dash"
        flags = style_flags(picks, {"finish_reason": "length"})
        for expected in (
            "output truncated",
            "exclamation mark",
            "em/en dash",
            "stock phrase: stunning",
            "'you said' phrasing",
            "generic caveat",
            "1 repeated opener(s)",
        ):
            with self.subTest(flag=expected):
                self.assertIn(expected, flags)

    def test_flags_clean_picks_as_clean(self) -> None:
        self.assertEqual(style_flags(DISCOVERY, {"finish_reason": "stop"}), [])


class CostTests(TestCase):
    def test_cost_prefers_provider_reported_value(self) -> None:
        case: Case = {"model": "m", "price": {"input": 2.0, "output": 4.0}}
        meta: dict[str, object] = {
            "usage": {"cost": 0.01, "prompt_tokens": 100, "completion_tokens": 10}
        }
        self.assertEqual(cost_for(case, meta), (0.01, "reported"))

    def test_cost_falls_back_to_price_table(self) -> None:
        case: Case = {"model": "m", "price": {"input": 2.0, "output": 4.0}}
        meta: dict[str, object] = {
            "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 1_000_000}
        }
        self.assertEqual(cost_for(case, meta), (6.0, "estimated"))

    def test_cost_is_none_without_price_or_usage(self) -> None:
        self.assertEqual(cost_for({"model": "m"}, {"usage": {}}), (None, ""))
        self.assertEqual(cost_for({"model": "m"}, {}), (None, ""))

    def test_cost_is_none_for_non_numeric_token_counts(self) -> None:
        case: Case = {"model": "m", "price": {"input": 2.0, "output": 4.0}}
        for usage in (
            {"prompt_tokens": None, "completion_tokens": 10},
            {"prompt_tokens": "100", "completion_tokens": 10},
            {"completion_tokens": 10},
        ):
            with self.subTest(usage=usage):
                self.assertEqual(cost_for(case, {"usage": usage}), (None, ""))


class MetaTests(TestCase):
    def test_ask_provider_records_usage_in_meta(self) -> None:
        usage = {"prompt_tokens": 111, "completion_tokens": 22, "total_tokens": 133}
        meta: dict[str, object] = {}
        picks = ask_provider(
            [{"game": "Portal 2", "feeling": "like", "reason": "Clever puzzles"}],
            meta=meta,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=provider_body(
                        {"replay": [], "backlog": [], "discover": DISCOVERY},
                        usage=usage,
                    ),
                )
            ),
        )
        self.assertEqual(len(picks), 3)
        self.assertEqual(meta["usage"], usage)
        self.assertEqual(meta["finish_reason"], "stop")


class CommandTests(TestCase):
    def test_command_passes_case_settings_and_writes_report(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "app.management.commands.eval_recommendations.ask_provider",
                return_value=RUN_PICKS,
            ) as ask,
        ):
            out = Path(tmp) / "evals"
            call_command("eval_recommendations", f"--out={out}")

            self.assertEqual(ask.call_count, len(CASES) * len(PROFILES))
            first = ask.call_args_list[0]
            self.assertEqual(first.args[0], PROFILES[0]["taste"])
            self.assertEqual(first.kwargs["model"], CASES[0]["model"])
            self.assertEqual(first.kwargs["reasoning_effort"], CASES[0]["effort"])

            report = next(out.glob("report-*.md"))
            text = report.read_text()
            self.assertIn(PROFILES[0]["name"], text)
            self.assertIn(CASES[0]["model"], text)
            self.assertIn("**New game 0** (discover)", text)
            self.assertTrue(report.with_suffix(".json").exists())

    def test_command_survives_provider_errors(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch(
                "app.management.commands.eval_recommendations.ask_provider",
                side_effect=RecommendationError("The AI provider refused."),
            ),
        ):
            out = Path(tmp) / "evals"
            call_command("eval_recommendations", f"--out={out}")

            text = next(out.glob("report-*.md")).read_text()
            self.assertIn("The AI provider refused.", text)
