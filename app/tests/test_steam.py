from datetime import timedelta
from unittest.mock import patch

import httpx
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.library import refresh_catalogue, sync_account
from app.models import (
    CatalogueState,
    Game,
    GameIdentity,
    LinkedAccount,
    Ownership,
    Preference,
)
from app.steam import SteamError, catalogue_pages, owned_games, resolve_profile


@override_settings(
    STEAM_WEB_API_KEY="test-key",
    PASSWORD_HASHERS=["django.contrib.auth.hashers.MD5PasswordHasher"],
)
class SteamTests(TestCase):
    def setUp(self) -> None:
        self.user = get_user_model().objects.create_user("alice", password="pw")
        self.other = get_user_model().objects.create_user("bob", password="pw")
        self.account = LinkedAccount.objects.create(
            user=self.user, provider="steam", external_user_id="76561198000000000"
        )
        self.client.force_login(self.user)

    def test_web_api_profile_library_and_catalogue_contract(self) -> None:
        requests = []

        def respond(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            self.assertEqual(request.headers["x-webapi-key"], "test-key")
            if "ResolveVanityURL" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "response": {
                            "success": 1,
                            "steamid": self.account.external_user_id,
                        }
                    },
                )
            if "GetOwnedGames" in request.url.path:
                return httpx.Response(
                    200,
                    json={
                        "response": {
                            "game_count": 1,
                            "games": [{"appid": 620, "name": "Portal 2"}],
                        }
                    },
                )
            if "last_appid" in request.url.params:
                return httpx.Response(
                    200,
                    json={
                        "response": {
                            "apps": [{"appid": 730, "name": "Counter-Strike 2"}],
                            "have_more_results": False,
                        }
                    },
                )
            return httpx.Response(
                200,
                json={
                    "response": {
                        "apps": [{"appid": 620, "name": "Portal 2"}],
                        "have_more_results": True,
                        "last_appid": 620,
                    }
                },
            )

        transport = httpx.MockTransport(respond)
        self.assertEqual(
            resolve_profile(
                "https://steamcommunity.com/id/alice/", transport=transport
            ),
            self.account.external_user_id,
        )
        self.assertEqual(
            owned_games(self.account.external_user_id, transport=transport),
            {"620": "Portal 2"},
        )
        self.assertEqual(
            list(catalogue_pages(transport=transport)),
            [{"620": "Portal 2"}, {"730": "Counter-Strike 2"}],
        )
        self.assertEqual(
            list(catalogue_pages(modified_since=42, transport=transport)),
            [{"620": "Portal 2"}, {"730": "Counter-Strike 2"}],
        )
        self.assertEqual(len(requests), 6)
        self.assertNotIn("key", requests[1].url.params)
        self.assertEqual(
            [request.url.params["if_modified_since"] for request in requests[4:]],
            ["42", "42"],
        )

    def test_catalogue_refresh_uses_previous_successful_start_time(self) -> None:
        previous = timezone.now() - timedelta(days=1)
        state = CatalogueState.objects.create(provider="steam", last_synced_at=previous)
        with patch(
            "app.steam.catalogue_pages", return_value=iter([{"620": "Portal 2"}])
        ) as pages:
            refresh_catalogue()
        pages.assert_called_once_with(modified_since=int(previous.timestamp()) - 1)
        state.refresh_from_db()
        self.assertGreater(state.last_synced_at, previous)
        self.assertTrue(
            GameIdentity.objects.filter(provider="steam", external_id="620").exists()
        )
        saved = state.last_synced_at
        with (
            patch("app.steam.catalogue_pages", side_effect=SteamError("failed")),
            self.assertRaises(SteamError),
        ):
            refresh_catalogue()
        state.refresh_from_db()
        self.assertEqual(state.last_synced_at, saved)

    def test_sync_reconciles_ownership_without_touching_curation(self) -> None:
        game = Game.objects.create(title="My curated title")
        GameIdentity.objects.create(game=game, provider="steam", external_id="620")
        owned = Ownership.objects.create(account=self.account, game=game)
        pref = Preference.objects.create(
            user=self.user, game=game, sentiment=-1, reason="Too hard"
        )
        with patch(
            "app.steam.owned_games",
            return_value={"620": "Portal 2", "730": "Counter-Strike 2"},
        ):
            self.assertEqual(sync_account(self.account), 2)
        game.refresh_from_db()
        pref.refresh_from_db()
        self.account.refresh_from_db()
        self.assertEqual(game.title, "My curated title")
        self.assertEqual((pref.sentiment, pref.reason), (-1, "Too hard"))
        self.assertIsNotNone(self.account.last_synced_at)
        added = Ownership.objects.get(
            account=self.account, game__identities__external_id="730"
        )
        with patch("app.steam.owned_games", return_value={"730": "Counter-Strike 2"}):
            sync_account(self.account)
        owned.refresh_from_db()
        added.refresh_from_db()
        self.assertFalse(owned.is_active)
        self.assertTrue(added.is_active)
        self.assertTrue(
            Preference.objects.filter(pk=pref.pk, reason="Too hard").exists()
        )
        previous_sync = self.account.last_synced_at
        with (
            patch("app.steam.owned_games", side_effect=SteamError("private")),
            self.assertRaises(SteamError),
        ):
            sync_account(self.account)
        self.account.refresh_from_db()
        owned.refresh_from_db()
        self.assertEqual(self.account.last_synced_at, previous_sync)
        self.assertFalse(owned.is_active)
        with (
            patch("app.steam.owned_games", return_value={}),
            self.assertRaises(SteamError),
        ):
            sync_account(self.account)
        added.refresh_from_db()
        self.assertTrue(added.is_active)

    def test_ignore_is_explicit_and_excluded_from_recommendations(self) -> None:
        game = Game.objects.create(title="Owned game")
        Ownership.objects.create(account=self.account, game=game)
        pref = Preference.objects.create(
            user=self.user, game=game, sentiment=1, reason="Good story"
        )
        self.client.post(reverse("library_curate", args=[game.pk]), {"sentiment": 0})
        pref.refresh_from_db()
        self.assertEqual((pref.sentiment, pref.reason), (0, "Good story"))
        from app.recommendations import RecommendationError, create_run

        with (
            patch("app.recommendations.ask_provider") as ask,
            self.assertRaises(RecommendationError),
        ):
            create_run(self.user)
        ask.assert_not_called()
        self.client.force_login(self.other)
        self.assertEqual(
            self.client.post(
                reverse("library_curate", args=[game.pk]), {"sentiment": -1}
            ).status_code,
            404,
        )

    def test_lookup_uses_local_catalogue_and_title_fallback(self) -> None:
        local = Game.objects.create(title="Local game")
        with patch("app.views.refresh_catalogue") as refresh:
            response = self.client.post(reverse("game_search"), {"query": "Local"})
        self.assertContains(response, local.title)
        refresh.assert_not_called()
        with patch(
            "app.steam.catalogue_pages", return_value=iter([{"620": "Portal 2"}])
        ) as pages:
            self.client.post(reverse("catalogue_refresh"))
            response = self.client.post(reverse("game_search"), {"query": "Portal"})
        self.assertContains(response, "Portal 2")
        pages.assert_called_once()
        self.assertTrue(CatalogueState.objects.filter(provider="steam").exists())
        identity = GameIdentity.objects.get(provider="steam", external_id="620")
        self.assertEqual(identity.game.title, "Portal 2")
        response = self.client.post(reverse("game_search"), {"query": "PORTAL 2"})
        self.assertNotContains(response, "Add “Portal 2” yourself")
        self.client.post(
            reverse("preference_new"),
            {
                "subject": "Portal 2",
                "game_id": identity.game_id,
                "sentiment": -1,
                "reason": "Too tricky",
            },
        )
        with patch("app.steam.owned_games", return_value={"620": "Portal 2"}):
            sync_account(self.account)
        self.assertTrue(
            Ownership.objects.filter(account=self.account, game=identity.game).exists()
        )
        self.assertTrue(
            Preference.objects.filter(
                user=self.user, game=identity.game, reason="Too tricky"
            ).exists()
        )
        with patch("app.views.refresh_catalogue") as refresh:
            response = self.client.post(reverse("game_search"), {"query": "Unknown"})
        self.assertContains(response, "Add “Unknown” yourself")
        refresh.assert_not_called()
        self.client.post(
            reverse("preference_new"),
            {"subject": "Unknown", "sentiment": 1, "reason": "Novel"},
        )
        self.assertTrue(
            Preference.objects.filter(
                user=self.user, game__title="Unknown", reason="Novel"
            ).exists()
        )

    def test_library_sections_and_reason_prompt(self) -> None:
        unrated = Game.objects.create(title="Unrated")
        ignored = Game.objects.create(title="Ignored")
        Ownership.objects.bulk_create(
            [Ownership(account=self.account, game=game) for game in (unrated, ignored)]
        )
        Preference.objects.create(user=self.user, game=ignored, sentiment=0)
        manual = Game.objects.create(title="Manual favorite")
        Preference.objects.create(
            user=self.user, game=manual, sentiment=1, reason="Story"
        )
        disliked = Game.objects.create(title="Manual dislike")
        Preference.objects.create(
            user=self.user, game=disliked, sentiment=-1, reason="Too slow"
        )
        response = self.client.get(reverse("library"))
        self.assertEqual(
            [entry.game.title for entry in response.context["unrated"]], ["Unrated"]
        )
        self.assertEqual(
            [
                (label, [pref.game.title for pref in games])
                for label, games in response.context["rated_sections"]
            ],
            [
                ("Loved", []),
                ("Liked", ["Manual favorite"]),
                ("Disliked", ["Manual dislike"]),
            ],
        )
        self.assertEqual(
            [pref.game.title for pref in response.context["ignored"]], ["Ignored"]
        )
        self.assertNotContains(
            self.client.get(reverse("home")), "Import your Steam library"
        )
        response = self.client.post(
            reverse("library_curate", args=[unrated.pk]), {"sentiment": -1}
        )
        self.assertEqual(
            Preference.objects.filter(user=self.user, game=unrated).count(), 0
        )
        form = self.client.get(response["Location"])
        self.assertEqual(form.context["form"].initial["sentiment"], -1)
        self.assertContains(form, "Why?")
        self.client.post(
            response["Location"],
            {
                "subject": "Unrated",
                "game_id": unrated.pk,
                "sentiment": -1,
                "reason": "Too slow",
            },
        )
        self.assertTrue(
            Preference.objects.filter(
                user=self.user, game=unrated, sentiment=-1, reason="Too slow"
            ).exists()
        )

    def test_not_played_can_be_rated_loved_or_removed(self) -> None:
        game = Game.objects.create(title="Future favorite")
        Ownership.objects.create(account=self.account, game=game)
        self.client.post(reverse("library_curate", args=[game.pk]), {"sentiment": -2})
        preference = Preference.objects.get(user=self.user, game=game)
        self.assertEqual(preference.sentiment, -2)
        self.assertEqual(
            [
                p.game.title
                for p in self.client.get(reverse("library")).context["not_played"]
            ],
            ["Future favorite"],
        )
        url = (
            reverse("preference_edit", args=[preference.pk])
            + "?feeling=loved&next=library"
        )
        self.assertEqual(self.client.get(url).context["form"].initial["sentiment"], 2)
        self.client.post(
            url,
            {
                "subject": game.title,
                "game_id": game.pk,
                "sentiment": 2,
                "reason": "Exceptional",
            },
        )
        preference.refresh_from_db()
        self.assertEqual((preference.sentiment, preference.reason), (2, "Exceptional"))
        self.assertEqual(
            [
                p.game.title
                for p in self.client.get(reverse("library")).context["rated_sections"][
                    0
                ][1]
            ],
            ["Future favorite"],
        )
        self.client.post(
            reverse("preference_delete", args=[preference.pk]) + "?next=library"
        )
        self.assertFalse(Preference.objects.filter(pk=preference.pk).exists())

    def test_feedback_search_prefers_existing_steam_game(self) -> None:
        Game.objects.create(title="Portal 2")  # Older title-only record.
        steam_game = Game.objects.create(title="Portal 2")
        GameIdentity.objects.create(
            game=steam_game, provider="steam", external_id="620"
        )
        search = self.client.get(
            reverse("game_search"), {"query": "Portal 2", "feeling": "dislike"}
        )
        self.assertEqual(search.context["results"][0].pk, steam_game.pk)
        self.assertEqual(
            [game.title for game in search.context["results"]], ["Portal 2"]
        )
        self.assertNotContains(search, "Add “Portal 2” yourself")
        search = self.client.post(
            reverse("game_search"), {"query": "Portal 2", "feeling": "dislike"}
        )
        self.assertEqual(search.context["feeling"], "dislike")
        url = reverse("preference_new") + f"?game={steam_game.pk}&feeling=dislike"
        form = self.client.get(url).context["form"]
        self.assertEqual(
            (form.initial["game_id"], form.initial["sentiment"]), (steam_game.pk, -1)
        )
        self.client.post(
            url,
            {
                "subject": "Portal 2",
                "game_id": steam_game.pk,
                "sentiment": -1,
                "reason": "Too fast",
            },
        )
        self.assertTrue(
            Preference.objects.filter(
                user=self.user, game=steam_game, reason="Too fast"
            ).exists()
        )
        self.assertEqual(Game.objects.filter(title="Portal 2").count(), 2)

    def test_private_response_cannot_remove_owned_games(self) -> None:
        game = Game.objects.create(title="Safe game")
        owned = Ownership.objects.create(account=self.account, game=game)
        with self.assertRaises(SteamError):
            owned_games(
                self.account.external_user_id,
                transport=httpx.MockTransport(
                    lambda _: httpx.Response(200, json={"response": {}})
                ),
            )
        owned.refresh_from_db()
        self.assertTrue(owned.is_active)

    @override_settings(STEAM_WEB_API_KEY="")
    def test_manual_entry_works_without_steam_configuration(self) -> None:
        response = self.client.post(
            reverse("preference_new"),
            {
                "subject": "Non-Steam game",
                "sentiment": 1,
                "reason": "Fun",
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Preference.objects.filter(
                user=self.user, game__title="Non-Steam game"
            ).exists()
        )
