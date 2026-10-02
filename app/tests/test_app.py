import json
from datetime import timedelta
from typing import Never
from unittest.mock import call, patch

import httpx
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from django.utils import timezone

from app.models import (
    CatalogueState,
    DismissedSuggestion,
    Game,
    GameIdentity,
    LinkedAccount,
    Ownership,
    Preference,
    RecommendationRun,
)
from app.recommendations import (
    RecommendationError,
    ask_provider,
    create_run,
    get_recent_recommendations,
)
from app.views import game_by_title

GAMES = [
    {
        "title": "The Talos Principle",
        "rationale": "Its puzzle solving fits your reasons for liking Portal 2.",
        "drawback": "The philosophical story may be slower than you want.",
    }
]
DISCOVERY_GAMES = GAMES + [GAMES[0] | {"title": f"New game {n}"} for n in range(4)]


def response_with(content: dict) -> dict:
    return {
        "choices": [
            {"finish_reason": "stop", "message": {"content": json.dumps(content)}}
        ]
    }


@override_settings(PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"])
class AppTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user("alice")
        self.other = get_user_model().objects.create_user("bob")
        self.game = Game.objects.create(title="Portal 2")
        self.preference = Preference.objects.create(
            user=self.user, game=self.game, sentiment=1, reason="Clever puzzles"
        )
        self.client.force_login(self.user)

    def test_signup_and_taste_crud(self) -> None:
        self.client.logout()
        response = self.client.post(
            reverse("signup"),
            {
                "username": "new-player",
                "password1": "a-long-password-872",
                "password2": "a-long-password-872",
            },
            follow=True,
        )
        self.assertContains(response, "Add a game")
        self.client.post(
            reverse("preference_new"),
            {"subject": "Outer Wilds", "sentiment": 1, "reason": "Exploration"},
        )
        pref = Preference.objects.get(game__title="Outer Wilds")
        self.assertNotEqual(pref.user_id, self.user.pk)
        self.client.post(
            reverse("preference_edit", args=[pref.pk]),
            {
                "subject": "Outer Wilds",
                "sentiment": -1,
                "reason": "Actually, the time pressure",
            },
        )
        pref.refresh_from_db()
        self.assertEqual(pref.reason, "Actually, the time pressure")
        self.assertEqual(pref.sentiment, -1)
        self.client.post(reverse("preference_delete", args=[pref.pk]))
        self.assertFalse(Preference.objects.filter(pk=pref.pk).exists())

    def test_signup_errors_follow_their_fields_without_upfront_guidance(self) -> None:
        self.client.logout()
        self.assertNotContains(
            self.client.get(reverse("signup")),
            "Your password must contain at least 8 characters.",
        )
        response = self.client.post(
            reverse("signup"),
            {
                "username": "alice",
                "password1": "123",
                "password2": "123",
            },
        )
        html = response.content.decode()
        self.assertLess(
            html.index('name="username"'),
            html.index("A user with that username already exists."),
        )
        self.assertLess(
            html.index("A user with that username already exists."),
            html.index('name="password1"'),
        )
        self.assertLess(
            html.index('name="password2"'), html.index("This password is too short.")
        )

    def test_case_insensitive_duplicate_updates_existing_game(self) -> None:
        self.client.post(
            reverse("preference_new"),
            {"subject": "PORTAL 2", "sentiment": -1, "reason": "Changed my mind"},
        )
        self.assertEqual(Preference.objects.filter(user=self.user).count(), 1)
        self.preference.refresh_from_db()
        self.assertEqual(self.preference.sentiment, -1)
        self.assertEqual(self.preference.reason, "Changed my mind")

    def test_punctuation_insensitive_title_lookup_uses_index(self) -> None:
        game = Game.objects.create(title="Marvel’s Spider-Man Remastered")
        GameIdentity.objects.create(game=game, provider="steam", external_id="1817070")
        with self.assertNumQueries(1):
            self.assertEqual(game_by_title("Marvel's Spider-Man Remastered"), game)
        self.assertIn(
            "USING INDEX",
            Game.objects.filter(
                normalized_title="marvel s spider man remastered"
            ).explain(),
        )

    def test_literal_title_still_precedes_punctuation_variant(self) -> None:
        local = Game.objects.create(title="Marvel's Spider-Man Remastered")
        steam = Game.objects.create(title="Marvel’s Spider-Man Remastered")
        GameIdentity.objects.create(game=steam, provider="steam", external_id="1817070")
        self.assertEqual(game_by_title(local.title), local)

    def test_title_edit_updates_normalized_lookup(self) -> None:
        game = Game.objects.create(title="Original title")
        game.title = "Changed: Title"
        game.save(update_fields=["title"])
        self.assertEqual(game_by_title("Changed Title"), game)

    def test_displayed_times_include_browser_convertible_instants(self) -> None:
        now = timezone.now()
        run = RecommendationRun.objects.create(user=self.user, inputs={}, results=GAMES)
        LinkedAccount.objects.create(
            user=self.user, provider="steam", external_user_id="123", last_synced_at=now
        )
        CatalogueState.objects.create(provider="steam", last_synced_at=now)
        for url, stamp in (
            (reverse("home"), run.created_at),
            (reverse("history"), run.created_at),
            (reverse("library"), now),
            (reverse("settings"), now),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertContains(response, f'<time datetime="{stamp.isoformat()}"')
                self.assertContains(response, "/static/app/local-time.js")

    def test_recommendation_art_uses_matching_steam_appid(self) -> None:
        GameIdentity.objects.create(
            game=Game.objects.create(title="The Talos Principle"),
            provider="steam",
            external_id="257510",
        )
        run = RecommendationRun.objects.create(
            user=self.user,
            inputs={},
            results=GAMES + [GAMES[0] | {"title": "Unknown game"}],
        )

        image = (
            "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/"
            "257510/header.jpg"
        )
        response = self.client.get(reverse("run", args=[run.pk]))

        self.assertContains(
            response,
            image,
        )
        self.assertContains(response, "/games/art/257510/")
        self.assertContains(
            response,
            'href="https://store.steampowered.com/app/257510/" '
            'target="_blank" rel="noopener noreferrer">The Talos Principle</a>',
        )
        self.assertContains(response, "onerror=")
        self.assertContains(response, "Unknown game")
        self.assertNotContains(response, ">Unknown game</a>")
        self.assertEqual(
            response.content.decode().count('class="recommendation-art"'), 1
        )
        self.assertNotIn("image_url", run.results[0])

    def test_ambiguous_edition_art_uses_first_match(self) -> None:
        for appid, title in (
            ("55150", "Warhammer 40,000: Space Marine - Anniversary Edition"),
            ("3169520", "Warhammer 40,000: Space Marine - Master Crafted Edition"),
        ):
            GameIdentity.objects.create(
                game=Game.objects.create(title=title),
                provider="steam",
                external_id=appid,
            )
        run = RecommendationRun.objects.create(
            user=self.user,
            inputs={},
            results=[
                GAMES[0]
                | {"title": "Warhammer 40,000: Space Marine", "category": "discover"},
                GAMES[0]
                | {"title": "Warhammer 40,000: Space Marine", "category": "backlog"},
            ],
        )

        self.assertEqual(
            [result["steam_appid"] for result in run.illustrated_results],
            ["55150", "55150"],
        )
        self.assertContains(
            self.client.get(reverse("run", args=[run.pk])),
            "/steam/apps/55150/header.jpg",
        )

    def test_failed_steam_image_redirects_to_current_artwork(self) -> None:
        GameIdentity.objects.create(
            game=Game.objects.create(title="Forza Horizon 6"),
            provider="steam",
            external_id="2483190",
        )
        image = (
            "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/"
            "2483190/hash/header_alt_assets_4.jpg"
        )
        with patch("app.views.header_image_url", return_value=image) as lookup:
            response = self.client.get(reverse("steam_art", args=[2483190]))
        self.assertRedirects(response, image, fetch_redirect_response=False)
        lookup.assert_called_once_with("2483190")

        with patch("app.views.header_image_url") as lookup:
            missing = self.client.get(reverse("steam_art", args=[123456]))
        self.assertEqual(missing.status_code, 404)
        lookup.assert_not_called()

    def test_recommendation_art_matches_store_title_variants(self) -> None:
        for appid, title in (
            ("632470", "Disco Elysium - The Final Cut"),
            ("1888930", "The Last of Us™ Part I"),
            ("2531310", "The Last of Us™ Part II Remastered"),
            ("1449110", "The Outer Worlds 2"),
            ("1920490", "The Outer Worlds: Spacer's Choice Edition"),
            ("1593500", "God of War"),
            ("356190", "Middle-earth™: Shadow of War™"),
            ("1817070", "Marvel’s Spider-Man Remastered"),
        ):
            GameIdentity.objects.create(
                game=Game.objects.create(title=title),
                provider="steam",
                external_id=appid,
            )
        run = RecommendationRun.objects.create(
            user=self.user,
            inputs={},
            results=[
                GAMES[0] | {"title": "Disco Elysium"},
                GAMES[0] | {"title": "The Last of Us Part I"},
                GAMES[0] | {"title": "The Outer Worlds"},
                GAMES[0] | {"title": "God of War (2018)"},
                GAMES[0] | {"title": "Middle-earth: Shadow of War"},
                GAMES[0] | {"title": "Marvel's Spider-Man Remastered"},
            ],
        )

        response = self.client.get(reverse("run", args=[run.pk]))

        self.assertContains(response, "/steam/apps/632470/header.jpg")
        self.assertContains(response, "/steam/apps/1888930/header.jpg")
        self.assertContains(response, "/steam/apps/1920490/header.jpg")
        self.assertContains(response, "/steam/apps/1593500/header.jpg")
        self.assertContains(response, "/steam/apps/356190/header.jpg")
        self.assertContains(response, "/steam/apps/1817070/header.jpg")
        run.results = [GAMES[0] | {"title": "The Last of Us"}]
        self.assertEqual(run.illustrated_results[0]["steam_appid"], "")

    def test_recommendation_art_resolves_titles_in_a_batch(self) -> None:
        for appid, title in (
            ("632470", "Disco Elysium - The Final Cut"),
            ("1888930", "The Last of Us™ Part I"),
            ("1593500", "God of War"),
            ("42", "The Complete Edition"),
        ):
            GameIdentity.objects.create(
                game=Game.objects.create(title=title),
                provider="steam",
                external_id=appid,
            )
        run = RecommendationRun.objects.create(
            user=self.user,
            inputs={},
            results=[
                GAMES[0] | {"title": "Disco Elysium"},
                GAMES[0] | {"title": "The Last of Us Part I"},
                GAMES[0] | {"title": "God of War (2018)"},
                GAMES[0] | {"title": "Unknown game"},
                GAMES[0] | {"title": "The"},
            ],
        )
        with CaptureQueriesContext(connection) as queries:
            appids = [result["steam_appid"] for result in run.illustrated_results]
        self.assertEqual(len(queries), 2)
        self.assertEqual(appids, ["632470", "1888930", "1593500", "", ""])
        with connection.cursor() as cursor:
            cursor.execute("EXPLAIN QUERY PLAN " + queries[0]["sql"])
            plan = " ".join(row[3] for row in cursor.fetchall())
        self.assertIn("USING INDEX", plan)
        self.assertNotIn("SCAN app_game", plan)

    def test_replay_art_prefers_the_owned_steam_app_when_titles_duplicate(self) -> None:
        owned_game = Game.objects.create(title="Fallout: New Vegas")
        GameIdentity.objects.create(
            game=owned_game, provider="steam", external_id="22380"
        )
        GameIdentity.objects.create(
            game=Game.objects.create(title="Fallout: New Vegas"),
            provider="steam",
            external_id="22490",
        )
        account = LinkedAccount.objects.create(
            user=self.user, provider="steam", external_user_id="123"
        )
        Ownership.objects.create(account=account, game=owned_game)
        run = RecommendationRun.objects.create(
            user=self.user,
            inputs={},
            results=[GAMES[0] | {"title": "Fallout: New Vegas", "category": "replay"}],
        )

        self.assertEqual(run.illustrated_results[0]["steam_appid"], "22380")

    def test_recommendation_calls_provider_in_request_with_only_own_taste(self) -> None:
        Preference.objects.create(
            user=self.other,
            game=Game.objects.create(title="Private game"),
            sentiment=-1,
            reason="Private reason",
        )
        with patch("app.recommendations.ask_provider", return_value=GAMES) as ask:
            response = self.client.post(
                reverse("recommend"),
                {"context": "Something relaxing"},
                HTTP_HX_REQUEST="true",
            )
        ask.assert_called_once_with(
            [{"game": "Portal 2", "feeling": "like", "reason": "Clever puzzles"}],
            "Something relaxing",
            owned={
                "replay": ["Portal 2"],
                "backlog": [],
                "all": [],
                "known": ["Portal 2"],
                "dismissed": [],
            },
            last_recommendations=[],
            recent_recommendations=[],
        )
        self.assertContains(response, "The Talos Principle")
        self.assertContains(
            response, "/games/search/?query=The%20Talos%20Principle&amp;feeling=dislike"
        )
        self.assertNotContains(response, "Private game")
        self.assertNotContains(response, "hx-trigger")
        run = RecommendationRun.objects.get()
        self.assertEqual(run.user_id, self.user.pk)
        self.assertEqual(run.results, GAMES)
        self.assertEqual(run.inputs["request"], "Something relaxing")

    def test_normal_form_post_also_calls_ai(self) -> None:
        with patch("app.recommendations.ask_provider", return_value=GAMES) as ask:
            response = self.client.post(reverse("recommend"), {})
        self.assertRedirects(
            response, reverse("run", args=[RecommendationRun.objects.get().pk])
        )
        ask.assert_called_once()

    def test_no_saved_taste_does_not_call_model(self) -> None:
        self.client.force_login(self.other)
        with patch("app.recommendations.ask_provider") as ask:
            response = self.client.post(
                reverse("recommend"), {}, HTTP_HX_REQUEST="true"
            )
        self.assertContains(response, "Add a game you like or dislike first")
        ask.assert_not_called()

    def test_recommendation_failure_shows_error_without_saving_run(self) -> None:
        for htmx in (False, True):
            with (
                self.subTest(htmx=htmx),
                patch(
                    "app.recommendations.ask_provider",
                    side_effect=RecommendationError("AI provider is unavailable"),
                ),
            ):
                response = self.client.post(
                    reverse("recommend"),
                    {"context": "Puzzle games"},
                    HTTP_HX_REQUEST="true" if htmx else "false",
                )
                self.assertContains(response, "AI provider is unavailable")
        self.assertFalse(RecommendationRun.objects.exists())
        self.assertTrue(Preference.objects.filter(pk=self.preference.pk).exists())

    def test_account_isolation_and_feedback_prefill(self) -> None:
        run = RecommendationRun.objects.create(
            user=self.other, inputs={}, results=GAMES
        )
        other_pref = Preference.objects.create(
            user=self.other,
            game=Game.objects.create(title="Other"),
            sentiment=1,
            reason="Private",
        )
        for url in (
            reverse("run", args=[run.pk]),
            reverse("preference_edit", args=[other_pref.pk]),
            reverse("preference_new") + f"?run={run.pk}&index=0",
        ):
            self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(
            self.client.post(
                reverse("preference_delete", args=[other_pref.pk])
            ).status_code,
            404,
        )
        self.assertNotContains(self.client.get(reverse("home")), "Private")
        run.user = self.user
        run.save()
        response = self.client.get(
            reverse("preference_new"),
            {"run": str(run.pk), "index": "0", "feeling": "dislike"},
        )
        self.assertContains(response, "The Talos Principle")
        self.assertEqual(response.context["form"].initial["sentiment"], -1)
        self.assertEqual(
            Preference.objects.count(), 2
        )  # Prefill waits for the user's reason and save.

    def test_discovery_dismissal_and_undo_from_past_run(self) -> None:
        run = RecommendationRun.objects.create(
            user=self.user,
            inputs={},
            results=[
                GAMES[0] | {"category": "discover"},
                GAMES[0] | {"title": "Portal 2", "category": "replay"},
            ],
        )
        url = reverse("dismiss_suggestion", args=[run.pk])
        self.assertContains(
            self.client.get(reverse("run", args=[run.pk])), "Not interested"
        )
        self.assertEqual(
            self.client.post(
                url, {"title": "Portal 2", "action": "dismiss"}
            ).status_code,
            404,
        )
        self.client.post(url, {"title": "The Talos Principle", "action": "dismiss"})
        self.assertEqual(DismissedSuggestion.objects.filter(user=self.user).count(), 1)
        self.assertEqual(Preference.objects.filter(user=self.user).count(), 1)
        self.assertNotContains(
            self.client.get(reverse("library")), "The Talos Principle"
        )
        self.assertContains(
            self.client.get(reverse("run", args=[run.pk])), "Not interested."
        )
        self.assertContains(self.client.get(reverse("run", args=[run.pk])), "Undo")

        with patch("app.recommendations.ask_provider", return_value=[]) as ask:
            create_run(self.user)
        self.assertEqual(
            ask.call_args.kwargs["owned"]["dismissed"], ["The Talos Principle"]
        )

        self.client.force_login(self.other)
        self.assertEqual(
            self.client.post(
                url, {"title": "The Talos Principle", "action": "undo"}
            ).status_code,
            404,
        )
        self.client.force_login(self.user)
        self.client.post(url, {"title": "The Talos Principle", "action": "undo"})
        self.assertFalse(DismissedSuggestion.objects.filter(user=self.user).exists())
        self.assertNotContains(
            self.client.get(reverse("run", args=[run.pk])), "Not interested."
        )

    def test_hidden_suggestions_can_be_undone_without_finding_a_run(self) -> None:
        dismissal = DismissedSuggestion.objects.create(
            user=self.user, game=Game.objects.create(title="A hidden game")
        )
        other_dismissal = DismissedSuggestion.objects.create(
            user=self.other, game=Game.objects.create(title="Private hidden game")
        )
        history = self.client.get(reverse("history"))
        self.assertContains(history, "Hidden suggestions")
        self.assertContains(history, "A hidden game")
        self.assertNotContains(history, "Private hidden game")

        self.assertEqual(
            self.client.post(
                reverse("undo_dismissal", args=[other_dismissal.pk])
            ).status_code,
            404,
        )
        self.assertRedirects(
            self.client.post(reverse("undo_dismissal", args=[dismissal.pk])),
            reverse("history"),
        )
        self.assertFalse(DismissedSuggestion.objects.filter(pk=dismissal.pk).exists())
        self.assertTrue(
            DismissedSuggestion.objects.filter(pk=other_dismissal.pk).exists()
        )
        self.assertNotContains(self.client.get(reverse("history")), "A hidden game")

    def test_invalid_input_and_csrf(self) -> None:
        with patch("app.recommendations.ask_provider") as ask:
            response = self.client.post(
                reverse("recommend"), {"context": "x" * 1001}, HTTP_HX_REQUEST="true"
            )
        self.assertContains(response, "under 1,000 characters")
        ask.assert_not_called()
        self.assertEqual(
            self.client.get(reverse("preference_new"), {"run": "bad"}).status_code, 302
        )
        csrf = Client(enforce_csrf_checks=True)
        csrf.force_login(self.user)
        self.assertEqual(csrf.post(reverse("recommend")).status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("recommend")).status_code, 302)

    def test_ai_text_is_escaped(self) -> None:
        games = [GAMES[0] | {"title": "<script>alert(1)</script>"}]
        with patch("app.recommendations.ask_provider", return_value=games):
            response = self.client.post(
                reverse("recommend"), {}, HTTP_HX_REQUEST="true"
            )
        self.assertNotContains(response, "<script>alert(1)</script>")
        self.assertContains(response, "&lt;script&gt;")

    def test_openai_compatible_adapter_uses_configured_endpoint(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            self.assertEqual(
                str(request.url), "http://localhost:11434/v1/chat/completions"
            )
            self.assertNotIn("authorization", request.headers)
            payload = json.loads(request.content)
            self.assertEqual(payload["model"], "qwen3.5:latest")
            self.assertNotIn("tools", payload)
            self.assertEqual(payload["response_format"]["type"], "json_schema")
            self.assertEqual(
                payload["response_format"]["json_schema"]["name"], "suggestions"
            )
            self.assertTrue(payload["response_format"]["json_schema"]["strict"])
            self.assertEqual(payload["reasoning_effort"], "none")
            self.assertEqual(payload["max_tokens"], 1234)
            self.assertIn("Clever puzzles", payload["messages"][1]["content"])
            request_data = json.loads(payload["messages"][1]["content"])
            self.assertEqual(
                request_data["excluded_discovery_titles"],
                ["Portal 2", "Owned only", "Known only"],
            )
            self.assertNotIn("owned_titles", request_data)
            self.assertNotIn("known_titles", request_data)
            self.assertIn(
                "Return five discovery picks", payload["messages"][0]["content"]
            )
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json.dumps(
                                    {
                                        "replay": [],
                                        "backlog": [],
                                        "discover": DISCOVERY_GAMES,
                                    }
                                )
                            },
                        }
                    ]
                },
            )

        taste = [{"game": "Portal 2", "feeling": "like", "reason": "Clever puzzles"}]
        with override_settings(
            OPENAI_COMPATIBLE_BASE_URL="http://localhost:11434/v1",
            OPENAI_COMPATIBLE_MODEL="qwen3.5:latest",
            OPENAI_COMPATIBLE_REASONING_EFFORT="none",
            OPENAI_COMPATIBLE_MAX_TOKENS=1234,
        ):
            self.assertEqual(
                ask_provider(
                    taste,
                    owned={
                        "replay": [],
                        "backlog": [],
                        "all": ["Portal 2", "Owned only"],
                        "known": ["Portal 2", "Known only"],
                    },
                    transport=httpx.MockTransport(respond),
                ),
                [game | {"category": "discover"} for game in DISCOVERY_GAMES[:3]],
            )

    def test_adapter_rejects_invalid_truncated_and_overlong_output(self) -> None:
        bodies = [
            {},
            {"choices": []},
            {"choices": [{"finish_reason": "length"}]},
            {
                "choices": [
                    {"finish_reason": "stop", "message": {"content": "not json"}}
                ]
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "replay": [],
                                    "backlog": [],
                                    "discover": [
                                        GAMES[0] | {"title": "x" * 201},
                                        *DISCOVERY_GAMES[1:],
                                    ],
                                }
                            )
                        },
                    }
                ]
            },
            {
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {
                            "content": json.dumps(
                                {
                                    "replay": [GAMES[0]] * 4,
                                    "backlog": [],
                                    "discover": DISCOVERY_GAMES,
                                }
                            )
                        },
                    }
                ]
            },
        ]
        for body in bodies:
            with self.subTest(body=body), self.assertRaises(RecommendationError):
                ask_provider(
                    [],
                    transport=httpx.MockTransport(
                        lambda request, body=body: httpx.Response(200, json=body)
                    ),
                )
        with self.assertRaises(RecommendationError):
            ask_provider(
                [], transport=httpx.MockTransport(lambda request: httpx.Response(503))
            )

        def timeout(request: httpx.Request) -> Never:
            raise httpx.ReadTimeout("timed out")

        with self.assertRaises(RecommendationError):
            ask_provider([], transport=httpx.MockTransport(timeout))

    def test_adapter_omits_duplicates_and_already_known_games(self) -> None:
        games = [GAMES[0], GAMES[0]] + [
            GAMES[0] | {"title": "Portal 2"} for _ in range(3)
        ]
        response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {"replay": [], "backlog": [], "discover": games}
                        )
                    },
                }
            ]
        }
        result = ask_provider(
            [{"game": "portal 2"}],
            last_recommendations=["Some unrelated recent pick"],
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=response)
            ),
        )
        self.assertEqual(result, [GAMES[0] | {"category": "discover"}])

    def test_last_run_titles_are_not_repeated(self) -> None:
        """Picks from the most recent run must not come back in the next one."""
        last_run = [
            {"category": "replay", "title": "Portal 2"},
            {"category": "backlog", "title": "Backlog game"},
            {"category": "discover", "title": "New game 0"},
        ]
        with (
            patch("app.recommendations.ask_provider", return_value=last_run),
            patch("app.recommendations.get_recent_recommendations", return_value=[]),
        ):
            create_run(self.user)

        def respond(request: httpx.Request) -> httpx.Response:
            data = json.loads(json.loads(request.content)["messages"][1]["content"])
            self.assertIn("Portal 2", data["last_recommendations"])
            self.assertIn("New game 0", data["last_recommendations"])
            return httpx.Response(
                200,
                json=response_with(
                    {
                        "replay": [GAMES[0] | {"title": "Portal 2"}],
                        "backlog": [
                            {
                                "title": "Backlog game",
                                "rationale": "Good fit",
                                "drawback": "Maybe slow",
                            }
                        ],
                        "discover": DISCOVERY_GAMES,
                    }
                ),
            )

        owned = {
            "replay": ["Portal 2"],
            "backlog": ["Backlog game"],
            "all": ["Backlog game", "Portal 2"],
            "known": ["Backlog game", "Portal 2"],
            "dismissed": [],
        }
        picks = ask_provider(
            [{"game": "Portal 2", "feeling": "loved", "reason": "Puzzles"}],
            owned=owned,
            last_recommendations=["Portal 2", "Backlog game", "New game 0"],
            transport=httpx.MockTransport(respond),
        )
        # "New game 0" was recommended last run and is dropped, while replay
        # and backlog repeat because each candidate list has one game.
        self.assertEqual(
            [(pick["category"], pick["title"]) for pick in picks],
            [
                ("discover", "The Talos Principle"),
                ("discover", "New game 1"),
                ("discover", "New game 2"),
                ("backlog", "Backlog game"),
                ("replay", "Portal 2"),
            ],
        )

    def test_recent_recommendations_exclude_does_not_consume_limit(self) -> None:
        """Last-run titles are skipped so the limit covers only older runs."""

        def picks(prefix: str, count: int) -> list[dict[str, str]]:
            return [
                {"category": "discover", "title": f"{prefix} {n}"} for n in range(count)
            ]

        for offset, (prefix, count) in enumerate(
            [
                ("Last", 9),
                ("Second", 9),
                ("Third", 9),
                ("Fourth", 9),
                ("Fifth", 9),
            ]
        ):
            run = RecommendationRun.objects.create(
                user=self.user, inputs={}, results=picks(prefix, count)
            )
            RecommendationRun.objects.filter(pk=run.pk).update(
                created_at=timezone.now() - timedelta(minutes=10 + offset)
            )

        last_run = get_recent_recommendations(self.user, runs=1)
        self.assertEqual(len(last_run), 9)

        # limit=15 applies to older titles only: 9 from the second run plus
        # 6 from the third, and none of the last run's titles.
        older = get_recent_recommendations(
            self.user, runs=5, limit=15, exclude=last_run
        )
        self.assertEqual(
            [title.removeprefix("Second ").removeprefix("Third ") for title in older],
            [str(n) for n in range(9)] + [str(n) for n in range(6)],
        )

    def test_recent_recommendations_are_passed_and_not_hard_blocked(self) -> None:
        """Older runs' titles reach the prompt as a soft signal, not a filter."""
        response = response_with(
            {"replay": [], "backlog": [], "discover": DISCOVERY_GAMES}
        )

        def respond(request: httpx.Request) -> httpx.Response:
            data = json.loads(json.loads(request.content)["messages"][1]["content"])
            self.assertEqual(data["recent_recommendations"], ["Older pick"])
            return httpx.Response(200, json=response)

        picks = ask_provider(
            [],
            last_recommendations=["The Talos Principle"],
            recent_recommendations=["Older pick"],
            transport=httpx.MockTransport(respond),
        )
        # "The Talos Principle" is hard-blocked; the soft list does not
        # filter anything.
        self.assertEqual(
            [pick["title"] for pick in picks],
            ["New game 0", "New game 1", "New game 2"],
        )

        # A title in only the soft list stays fully eligible for replay.
        picks = ask_provider(
            [],
            owned={
                "replay": ["Older pick"],
                "backlog": [],
                "all": [],
                "known": [],
            },
            last_recommendations=["The Talos Principle"],
            recent_recommendations=["Older pick"],
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json=response_with(
                        {
                            "replay": [GAMES[0] | {"title": "Older pick"}],
                            "backlog": [],
                            "discover": DISCOVERY_GAMES,
                        }
                    ),
                )
            ),
        )
        self.assertEqual(
            [(pick["category"], pick["title"]) for pick in picks],
            [
                ("discover", "New game 0"),
                ("discover", "New game 1"),
                ("discover", "New game 2"),
                ("replay", "Older pick"),
            ],
        )

        # create_run passes both lists separately.
        with (
            patch("app.recommendations.ask_provider", return_value=[]) as ask,
            patch(
                "app.recommendations.get_recent_recommendations",
                side_effect=[["Last run"], ["Older pick"]],
            ) as get_recent,
        ):
            create_run(self.user)
        self.assertEqual(
            get_recent.call_args_list,
            [
                call(user=self.user, runs=1),
                call(user=self.user, runs=5, limit=15, exclude=["Last run"]),
            ],
        )
        self.assertEqual(ask.call_args.kwargs["last_recommendations"], ["Last run"])
        self.assertEqual(ask.call_args.kwargs["recent_recommendations"], ["Older pick"])

    def test_all_recent_picks_fail_instead_of_duplicating(self) -> None:
        """When filtering empties every group, the request fails loudly."""
        response = response_with(
            {
                "replay": [
                    GAMES[0] | {"title": "Replay 0"},
                    GAMES[0] | {"title": "Replay 1"},
                    GAMES[0] | {"title": "Replay 2"},
                ],
                "backlog": [],
                "discover": DISCOVERY_GAMES,
            }
        )
        owned = {
            "replay": ["Portal 2", "Replay 0", "Replay 1", "Replay 2"],
            "backlog": [],
            "all": ["Portal 2", "Replay 0", "Replay 1", "Replay 2"],
            "known": [],
            "dismissed": [],
        }
        with self.assertRaises(RecommendationError):
            ask_provider(
                [{"game": "Portal 2", "feeling": "loved", "reason": "Puzzles"}],
                owned=owned,
                last_recommendations=[
                    "Replay 0",
                    "Replay 1",
                    "Replay 2",
                    *(game["title"] for game in DISCOVERY_GAMES),
                ],
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=response)
                ),
            )

    def test_dismissed_titles_reach_provider_and_are_filtered(self) -> None:
        def respond(request: httpx.Request) -> httpx.Response:
            data = json.loads(json.loads(request.content)["messages"][1]["content"])
            self.assertIn("The Talos Principle", data["excluded_discovery_titles"])
            return httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {
                                "content": json.dumps(
                                    {
                                        "replay": [],
                                        "backlog": [],
                                        "discover": DISCOVERY_GAMES,
                                    }
                                )
                            },
                        }
                    ]
                },
            )

        picks = ask_provider(
            [],
            owned={
                "replay": [],
                "backlog": [],
                "all": [],
                "known": [],
                "dismissed": ["The Talos Principle"],
            },
            transport=httpx.MockTransport(respond),
        )
        self.assertEqual(
            [pick["title"] for pick in picks],
            ["New game 0", "New game 1", "New game 2"],
        )

    def test_owned_picks_are_verified_and_loved_is_a_strong_signal(self) -> None:
        account = LinkedAccount.objects.create(
            user=self.user, provider="steam", external_user_id="123"
        )
        backlog = Game.objects.create(title="Backlog game")
        ignored = Game.objects.create(title="Ignored game")
        Ownership.objects.bulk_create(
            [
                Ownership(account=account, game=game)
                for game in (self.game, backlog, ignored)
            ]
        )
        self.preference.sentiment = 2
        self.preference.save()
        Preference.objects.create(user=self.user, game=backlog, sentiment=-2)
        Preference.objects.create(user=self.user, game=ignored, sentiment=0)
        suggestions = [
            {"category": "discover", "title": "Portal 2"},
            {"category": "replay", "title": "Portal 2"},
            {"category": "backlog", "title": "Backlog game"},
            {"category": "discover", "title": "Ignored game"},
            {"category": "replay", "title": "Unknown owned game"},
            {"category": "discover", "title": "New game"},
            {"category": "discover", "title": "Portal 2"},
            {"category": "discover", "title": "Ignored game"},
        ]
        response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                category: [
                                    {
                                        "title": suggestion["title"],
                                        "rationale": "Fits your taste",
                                        "drawback": "May be slow",
                                    }
                                    for suggestion in suggestions
                                    if suggestion["category"] == category
                                ]
                                for category in ("replay", "backlog", "discover")
                            }
                        )
                    },
                }
            ]
        }
        owned = {
            "replay": ["Portal 2"],
            "backlog": ["Backlog game"],
            "all": ["Backlog game", "Ignored game", "Portal 2"],
            "known": ["Ignored game", "Backlog game", "Portal 2"],
            "dismissed": [],
        }
        picks = ask_provider(
            [{"game": "Portal 2", "feeling": "loved", "reason": "Clever puzzles"}],
            owned=owned,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=response)
            ),
        )
        self.assertEqual(
            [(pick["category"], pick["title"]) for pick in picks],
            [
                ("discover", "New game"),
                ("backlog", "Backlog game"),
                ("replay", "Portal 2"),
            ],
        )
        with patch("app.recommendations.ask_provider", return_value=picks) as ask:
            run = create_run(self.user)
        self.assertEqual(
            ask.call_args.args[0],
            [{"game": "Portal 2", "feeling": "loved", "reason": "Clever puzzles"}],
        )
        self.assertEqual(ask.call_args.kwargs["owned"], owned)
        run.results.reverse()  # Older saved runs may have the opposite order.
        run.save(update_fields=["results"])
        page = self.client.get(reverse("run", args=[run.pk]))
        self.assertContains(page, "Your picks")
        self.assertContains(page, "Favourites to revisit")
        self.assertContains(page, "Games you haven't played yet")
        self.assertContains(page, "New games to try")
        html = page.content.decode()
        self.assertLess(
            html.index("New games to try"),
            html.index("Games you haven't played yet"),
        )
        self.assertLess(
            html.index("Games you haven't played yet"),
            html.index("Favourites to revisit"),
        )
        home = self.client.get(reverse("home"))
        self.assertContains(home, "Latest picks")
        self.assertContains(home, 'class="has-picks"')

    def test_manually_added_games_are_replay_and_backlog_candidates(self) -> None:
        backlog = Game.objects.create(title="Family-shared backlog")
        dislike = Game.objects.create(title="Disliked game")
        Preference.objects.create(user=self.user, game=backlog, sentiment=-2)
        Preference.objects.create(user=self.user, game=dislike, sentiment=-1)
        with patch("app.recommendations.ask_provider", return_value=[]) as ask:
            create_run(self.user)
        self.assertEqual(ask.call_args.kwargs["owned"]["replay"], ["Portal 2"])
        self.assertEqual(
            ask.call_args.kwargs["owned"]["backlog"], ["Family-shared backlog"]
        )

    def test_recommender_accepts_three_picks_in_each_group(self) -> None:
        owned = {
            "replay": [f"Replay {n}" for n in range(3)],
            "backlog": [f"Backlog {n}" for n in range(3)],
            "all": [f"Replay {n}" for n in range(3)]
            + [f"Backlog {n}" for n in range(3)],
            "known": [],
        }
        games = [
            {
                "category": category,
                "title": f"{name} {n}",
                "rationale": "Good fit",
                "drawback": "Maybe slow",
            }
            for category, name in (
                ("replay", "Replay"),
                ("backlog", "Backlog"),
                ("discover", "New"),
            )
            for n in range(5 if category == "discover" else 3)
        ]
        response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                category: [
                                    {
                                        key: value
                                        for key, value in game.items()
                                        if key != "category"
                                    }
                                    for game in games
                                    if game["category"] == category
                                ]
                                for category in ("replay", "backlog", "discover")
                            }
                        )
                    },
                }
            ]
        }
        result = ask_provider(
            [],
            owned=owned,
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=response)
            ),
        )
        self.assertEqual(result, games[6:9] + games[3:6] + games[:3])

    def test_recommender_rejects_fewer_than_five_discovery_candidates(self) -> None:
        response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "replay": [GAMES[0] | {"title": "Portal 2"}],
                                "backlog": [],
                                "discover": [],
                            }
                        )
                    },
                }
            ]
        }
        with self.assertRaises(RecommendationError):
            ask_provider(
                [],
                owned={"replay": ["Portal 2"], "backlog": [], "all": [], "known": []},
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=response)
                ),
            )
