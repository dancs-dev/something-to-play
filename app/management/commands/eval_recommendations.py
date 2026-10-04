"""Evaluate the recommendation prompt across models, efforts and test profiles."""

import argparse
import json
import re
import time
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import NotRequired, TypedDict

from django.core.management.base import BaseCommand

from app.recommendations import (
    PROMPT_VERSION,
    STOCK_PHRASES,
    Owned,
    Pick,
    RecommendationError,
    Taste,
    ask_provider,
)


# One case is one (model, effort) pair. base_url/api_key default to the
# OPENAI_COMPATIBLE_* settings, so point your .env at OpenRouter or override
# them per case to mix endpoints. price is USD per million tokens, used when
# the provider does not report a cost itself. Prices fetched from
# openrouter.ai/api/v1/models on 2026-10-03; re-check when you swap models.
class Case(TypedDict):
    model: str
    effort: NotRequired[str]
    base_url: NotRequired[str]
    api_key: NotRequired[str]
    price: NotRequired[dict[str, float]]


class Profile(TypedDict):
    name: str
    request: str
    taste: list[Taste]
    owned: Owned
    last_recommendations: list[str]
    recent_recommendations: list[str]


CASES: list[Case] = [
    {
        "model": "deepseek/deepseek-v4.1-flash",
        "effort": "high",
    },
    {
        "model": "deepseek/deepseek-v4.1-flash",
        "effort": "xhigh",
    },
    {
        "model": "z-ai/glm-5.3-flash",
        "effort": "high",
    },
    {
        "model": "z-ai/glm-5.3-flash",
        "effort": "max",
    },
    {
        "model": "z-ai/glm-5.3-flash",
        "effort": "low",
    },
    # effort "" (or omitting the key) omits the reasoning_effort parameter;
    # also add "base_url" and "api_key" to override the OPENAI_COMPATIBLE_*
    # settings per case, e.g. "base_url": "http://localhost:11434/v1" for
    # local Ollama models and "api_key": "" to send no Authorization header.
]


# Synthetic test players, generated with AI: the dislikes and complaints are
# the negative signal the prompt is meant to reason about.
#
# Profiles mirror what create_run sends the provider: replay is every liked
# or loved game, backlog is every "not played yet" rating, known is every
# rated game plus the backlog, and all is the Steam library, which rated
# games may fall outside. Ratings use the app's wording: loved, like, dislike.
PROFILES: list[Profile] = [
    {
        # Realistic library: some purchases never touched, one bounced off.
        "name": "tinkerer",
        "request": "",
        "taste": [
            {
                "game": "Factorio",
                "feeling": "loved",
                "reason": "200 hours. belt maths finally clicked",
            },
            {
                "game": "Satisfactory",
                "feeling": "like",
                "reason": "same itch but first person. runs badly late game",
            },
            {
                "game": "Stardew Valley",
                "feeling": "dislike",
                "reason": "felt like a second job",
            },
            {
                "game": "Hollow Knight",
                "feeling": "loved",
                "reason": "exploring is great, hard but fair",
            },
            {"game": "Celeste", "feeling": "like", "reason": "instant restarts"},
            {
                "game": "Baba Is You",
                "feeling": "like",
                "reason": "got stuck, looked a few up",
            },
        ],
        "owned": {
            "replay": [
                "Factorio",
                "Satisfactory",
                "Hollow Knight",
                "Celeste",
                "Baba Is You",
            ],
            "backlog": ["Outer Wilds", "Balatro", "Dishonored", "Cities: Skylines"],
            "all": [
                "Factorio",
                "Satisfactory",
                "Hollow Knight",
                "Celeste",
                "Baba Is You",
                "Stardew Valley",
                "Outer Wilds",
                "Balatro",
                "Dishonored",
                "Cities: Skylines",
                "Portal 2",
                "Vampire Survivors",
            ],
            # Portal 2 is played but the user never gave an opinion on it.
            "known": [
                "Factorio",
                "Satisfactory",
                "Stardew Valley",
                "Hollow Knight",
                "Celeste",
                "Baba Is You",
                "Outer Wilds",
                "Balatro",
                "Dishonored",
                "Cities: Skylines",
                "Portal 2",
            ],
            "dismissed": ["Coral Island"],
        },
        "last_recommendations": ["Dyson Sphere Program", "Dead Cells"],
        "recent_recommendations": ["Rimworld", "Terraria"],
    },
    {
        # Casual, slightly vague request with a soft constraint.
        "name": "couch-and-story",
        "request": (
            "me and my wife want something to play on the couch, short "
            "sessions, nothing too hard"
        ),
        "taste": [
            {
                "game": "It Takes Two",
                "feeling": "loved",
                "reason": "best thing we've played together",
            },
            {
                "game": "Overcooked 2",
                "feeling": "like",
                "reason": "fun but we shout at each other",
            },
            {
                "game": "Divinity: Original Sin 2",
                "feeling": "like",
                "reason": "great co-op but sessions ran way too long",
            },
            {
                "game": "Ori and the Will of the Wisps",
                "feeling": "loved",
                "reason": "played this one solo. gorgeous",
            },
            {
                "game": "Disco Elysium",
                "feeling": "dislike",
                "reason": "too much reading",
            },
        ],
        "owned": {
            "replay": [
                "It Takes Two",
                "Overcooked 2",
                "Divinity: Original Sin 2",
                "Ori and the Will of the Wisps",
            ],
            "backlog": ["Unravel Two", "Brothers: A Tale of Two Sons"],
            "all": [
                "It Takes Two",
                "Overcooked 2",
                "Divinity: Original Sin 2",
                "Ori and the Will of the Wisps",
                "Disco Elysium",
                "Unravel Two",
                "Brothers: A Tale of Two Sons",
            ],
            "known": [
                "It Takes Two",
                "Overcooked 2",
                "Divinity: Original Sin 2",
                "Ori and the Will of the Wisps",
                "Disco Elysium",
                "Unravel Two",
                "Brothers: A Tale of Two Sons",
            ],
            "dismissed": ["Cuphead"],
        },
        "last_recommendations": ["Castle Crashers", "Lovers in a Dangerous Spacetime"],
        "recent_recommendations": ["Rayman Legends"],
    },
    {
        # Very thin data, no request.
        "name": "minimal",
        "request": "",
        "taste": [
            {
                "game": "Tetris Effect",
                "feeling": "like",
                "reason": "nice for short bits on the deck",
            },
            {"game": "Hades", "feeling": "loved", "reason": "one more run"},
        ],
        "owned": {
            "replay": ["Tetris Effect", "Hades"],
            "backlog": ["Slay the Spire"],
            "all": ["Tetris Effect", "Hades", "Slay the Spire"],
            "known": ["Tetris Effect", "Hades", "Slay the Spire"],
            "dismissed": [],
        },
        "last_recommendations": [],
        "recent_recommendations": [],
    },
    {
        # Request pulls against stated taste; some reasons are blank.
        "name": "cosy-but-spooky",
        "request": "want something properly spooky for halloween but i'm a wimp",
        "taste": [
            {
                "game": "Animal Crossing: New Horizons",
                "feeling": "loved",
                "reason": "play it every day, so relaxing",
            },
            {
                "game": "Luigi's Mansion 3",
                "feeling": "loved",
                "reason": "spooky but silly, never actually scared me",
            },
            {
                "game": "Resident Evil 4",
                "feeling": "dislike",
                "reason": "couldn't relax, quit early",
            },
            {
                "game": "Untitled Goose Game",
                "feeling": "like",
                "reason": "short and funny",
            },
            {"game": "Unpacking", "feeling": "like", "reason": ""},
        ],
        "owned": {
            "replay": [
                "Animal Crossing: New Horizons",
                "Luigi's Mansion 3",
                "Untitled Goose Game",
                "Unpacking",
            ],
            "backlog": ["Luigi's Mansion 2 HD"],
            "all": [
                "Animal Crossing: New Horizons",
                "Luigi's Mansion 3",
                "Luigi's Mansion 2 HD",
                "Resident Evil 4",
                "Untitled Goose Game",
                "Unpacking",
            ],
            "known": [
                "Animal Crossing: New Horizons",
                "Luigi's Mansion 3",
                "Luigi's Mansion 2 HD",
                "Resident Evil 4",
                "Untitled Goose Game",
                "Unpacking",
            ],
            "dismissed": ["Outlast"],
        },
        "last_recommendations": ["Little Nightmares"],
        "recent_recommendations": [],
    },
    {
        # Big library, asks for help choosing from what they own.
        # Owns Elden Ring despite disliking Dark Souls (bought on hype).
        "name": "backlog-hoarder",
        "request": "i own way too much. what should i actually play next?",
        "taste": [
            {"game": "Skyrim", "feeling": "loved", "reason": "modded it to death"},
            {
                "game": "The Witcher 3",
                "feeling": "loved",
                "reason": "finished it twice",
            },
            {
                "game": "Dark Souls",
                "feeling": "dislike",
                "reason": "died to the first boss for an hour and uninstalled",
            },
            {"game": "Civilization VI", "feeling": "like", "reason": "one more turn"},
            {
                "game": "Baldur's Gate 3",
                "feeling": "like",
                "reason": "got to act 2 and drifted off",
            },
            {
                "game": "Cyberpunk 2077",
                "feeling": "like",
                "reason": "good after the patches",
            },
        ],
        "owned": {
            "replay": [
                "Skyrim",
                "The Witcher 3",
                "Civilization VI",
                "Baldur's Gate 3",
                "Cyberpunk 2077",
            ],
            "backlog": [
                "Red Dead Redemption 2",
                "Mass Effect Legendary Edition",
                "Disco Elysium",
                "Hades",
                "Death Stranding",
                "Fallout: New Vegas",
                "Elden Ring",
                "Pillars of Eternity",
            ],
            "all": [
                "Skyrim",
                "The Witcher 3",
                "Dark Souls",
                "Civilization VI",
                "Baldur's Gate 3",
                "Cyberpunk 2077",
                "Red Dead Redemption 2",
                "Mass Effect Legendary Edition",
                "Disco Elysium",
                "Hades",
                "Death Stranding",
                "Fallout: New Vegas",
                "Elden Ring",
                "Pillars of Eternity",
            ],
            "known": [
                "Skyrim",
                "The Witcher 3",
                "Dark Souls",
                "Civilization VI",
                "Baldur's Gate 3",
                "Cyberpunk 2077",
                "Red Dead Redemption 2",
                "Mass Effect Legendary Edition",
                "Disco Elysium",
                "Hades",
                "Death Stranding",
                "Fallout: New Vegas",
                "Elden Ring",
                "Pillars of Eternity",
            ],
            "dismissed": [],
        },
        "last_recommendations": ["Kingdom Come: Deliverance"],
        "recent_recommendations": ["Dragon Age: Inquisition"],
    },
    {
        # Niche taste: guards against popularity bias.
        # Dwarf Fortress was played via the free classic version, so it is
        # known but not owned. The app still offers every liked or loved
        # game as a replay candidate, owned or not.
        "name": "niche-sim-fan",
        "request": "",
        "taste": [
            {
                "game": "Dwarf Fortress",
                "feeling": "loved",
                "reason": "lost three fortresses and the stories are why I play",
            },
            {
                "game": "Caves of Qud",
                "feeling": "loved",
                "reason": "the writing and the weirdness",
            },
            {"game": "Cogmind", "feeling": "like", "reason": "UI took a week to learn"},
            {"game": "Disco Elysium", "feeling": "loved", "reason": "read every line"},
            {
                "game": "Fallout 4",
                "feeling": "dislike",
                "reason": "the shooting was the boring part",
            },
            {"game": "Hades", "feeling": "dislike", "reason": "just not for me"},
        ],
        "owned": {
            "replay": [
                "Dwarf Fortress",
                "Caves of Qud",
                "Cogmind",
                "Disco Elysium",
            ],
            "backlog": ["Pathologic 2", "Tales of Maj'Eyal"],
            "all": [
                "Caves of Qud",
                "Cogmind",
                "Disco Elysium",
                "Fallout 4",
                "Hades",
                "Pathologic 2",
                "Tales of Maj'Eyal",
            ],
            "known": [
                "Dwarf Fortress",
                "Caves of Qud",
                "Cogmind",
                "Disco Elysium",
                "Fallout 4",
                "Hades",
                "Pathologic 2",
                "Tales of Maj'Eyal",
            ],
            "dismissed": [],
        },
        "last_recommendations": ["Project Zomboid"],
        "recent_recommendations": ["Dredge", "Into the Breach"],
    },
]

# Flag names follow the prompt's wording so report and prompt stay in sync.


def style_flags(
    picks: Sequence[Mapping[str, object]], meta: dict[str, object]
) -> list[str]:
    """Cheap regex checks for the prompt's hard Writing rules, not a judge."""
    flags: list[str] = []
    if meta.get("finish_reason") == "length":
        flags.append("output truncated")

    text = " ".join(
        str(pick.get(field, ""))
        for pick in picks
        for field in ("rationale", "drawback")
    )
    lowered = text.lower()

    if "!" in text:
        flags.append("exclamation mark")
    if "—" in text or "–" in text:
        flags.append("em/en dash")
    for phrase in STOCK_PHRASES:
        if re.search(rf"\b{phrase}\b", lowered):
            flags.append(f"stock phrase: {phrase}")
    if re.search(r"\byou (said|described|mentioned|noted)\b", lowered):
        flags.append("'you said' phrasing")
    if re.search(r"may not suit (everyone|all players)", lowered):
        flags.append("generic caveat")

    openers = [tuple(str(pick.get("rationale", "")).split()[:4]) for pick in picks]
    repeats = len(openers) - len(set(openers))
    if repeats:
        flags.append(f"{repeats} repeated opener(s)")
    return flags


def cost_for(case: Case, meta: dict[str, object]) -> tuple[float | None, str]:
    """Cost and its source: "reported" by the provider, "estimated" from the
    case's price table, or (None, "") when neither is possible."""
    usage = meta.get("usage")
    if not isinstance(usage, dict):
        return None, ""

    if usage.get("cost") is not None:
        try:
            return float(usage["cost"]), "reported"
        except (TypeError, ValueError):
            pass

    price = case.get("price")
    if price is None:
        return None, ""

    try:
        cost = (
            float(price["input"]) * usage["prompt_tokens"] / 1_000_000
            + float(price["output"]) * usage["completion_tokens"] / 1_000_000
        )
    except (TypeError, ValueError, KeyError):
        # Missing or non-numeric token counts: no estimate rather than a crash
        # or a partial one built from a zero count.
        return None, ""
    return cost, "estimated"


class Row(TypedDict):
    label: str
    profile: str
    seconds: float
    meta: dict[str, object]
    picks: list[Pick]
    flags: list[str]
    cost: float | None
    cost_source: str
    error: str


def run_cell(case: Case, profile: Profile) -> Row:
    """Run one case against one profile and collect its timing and result.

    Args:
        case: The model and optional effort, endpoint, key and price
          overrides.
        profile: The test player's taste, request and library.

    Returns:
        A row with the timing, provider metadata, filtered picks, style
        flags, cost and any error message.
    """
    meta: dict[str, object] = {}
    started = time.monotonic()
    error = ""
    picks: list[Pick] = []

    try:
        picks = ask_provider(
            profile["taste"],
            profile["request"],
            owned={
                "replay": profile["owned"]["replay"],
                "backlog": profile["owned"]["backlog"],
                "all": profile["owned"]["all"],
                "known": profile["owned"]["known"],
                "dismissed": profile["owned"]["dismissed"],
            },
            last_recommendations=profile["last_recommendations"],
            recent_recommendations=profile["recent_recommendations"],
            model=case["model"],
            reasoning_effort=case.get("effort", ""),
            base_url=case.get("base_url"),
            api_key=case.get("api_key"),
            meta=meta,
        )
    except RecommendationError as exc:
        error = str(exc)

    seconds = time.monotonic() - started
    flags = [] if error else style_flags(picks, meta)
    cost, cost_source = (None, "") if error else cost_for(case, meta)
    label = case["model"]
    if case.get("effort"):
        label += f" ({case['effort']} effort)"

    return {
        "label": label,
        "profile": profile["name"],
        "seconds": round(seconds, 1),
        "meta": meta,
        "picks": picks,
        "flags": flags,
        "cost": cost,
        "cost_source": cost_source,
        "error": error,
    }


def row_stats(row: Row) -> tuple[str, str]:
    """Tokens and cost strings, shared by the summary table and the detail."""
    usage = row["meta"].get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    prompt = usage.get("prompt_tokens", "?")
    completion = usage.get("completion_tokens", "?")
    tokens = f"{prompt} in / {completion} out"

    if row["cost"] is None:
        cost = "no cost"
    elif row["cost_source"] == "estimated":
        cost = f"${row['cost']:.4f} (est.)"
    else:
        cost = f"${row['cost']:.4f} (reported)"
    return tokens, cost


def write_report(rows: list[Row], out_dir: Path) -> tuple[Path, Path]:
    """Write the Markdown and JSON eval reports.

    Args:
        rows: The per-case, per-profile results to report.
        out_dir: Directory to write the reports into, created if missing.

    Returns:
        A tuple (report, data) of the Markdown and JSON report paths.
    """
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    report = out_dir / f"report-{stamp}.md"
    data = out_dir / f"report-{stamp}.json"
    out_dir.mkdir(parents=True, exist_ok=True)

    lines = [
        "# Recommendation eval",
        "",
        f"Prompt: {PROMPT_VERSION} | Generated: {datetime.now(UTC):%Y-%m-%d %H:%M} UTC",
        "",
        "| Case | Profile | Time | Tokens in/out | Cost | Flags | Error |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        tokens, cost = row_stats(row)
        flags = ", ".join(row["flags"]) or "—"
        lines.append(
            f"| {row['label']} | {row['profile']} | {row['seconds']}s | "
            f"{tokens} | {cost} | {flags} | {row['error'] or '—'} |"
        )

    for row in rows:
        lines += ["", f"## {row['label']} / {row['profile']}"]
        if row["error"]:
            lines.append(f"Error: {row['error']}")
            continue
        tokens, cost = row_stats(row)
        lines.append(
            f"{row['seconds']}s | {tokens} | {cost} | "
            + (", ".join(row["flags"]) or "no style flags")
        )
        lines.append("")
        for pick in row["picks"]:
            lines.append(
                f"- **{pick['title']}** ({pick['category']})\n"
                f"  {pick['rationale']}\n"
                f"  *You might not enjoy:* {pick['drawback']}"
            )

    report.write_text("\n".join(lines) + "\n")
    data.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(UTC).isoformat(),
                "prompt_version": PROMPT_VERSION,
                # Cases verbatim, minus api_key: keys must not land in reports.
                "cases": [
                    {k: v for k, v in case.items() if k != "api_key"} for case in CASES
                ],
                "profiles": PROFILES,
                "rows": rows,
            },
            indent=2,
        )
        + "\n"
    )
    return report, data


class Command(BaseCommand):
    help = (
        "Run the recommendation prompt against test profiles for every case "
        "in CASES and write a Markdown and JSON report to the evals directory."
    )

    def add_arguments(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument(
            "--out", default="evals", help="Directory for the generated reports."
        )

    def handle(self, *args: str, **options: object) -> None:
        rows: list[Row] = []
        for case in CASES:
            for profile in PROFILES:
                row = run_cell(case, profile)
                rows.append(row)
                note = row["error"] or ", ".join(row["flags"]) or "clean"
                self.stdout.write(
                    f"{row['label']} / {row['profile']}: {row['seconds']}s, {note}"
                )

        report, data = write_report(rows, Path(str(options["out"])))
        self.stdout.write(self.style.SUCCESS(f"Wrote {report} and {data}"))
