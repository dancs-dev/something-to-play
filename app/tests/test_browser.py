"""Browser smoke test for the recommendation flow."""

from unittest.mock import patch

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from app.models import Game, GameIdentity, RecommendationRun
from app.tests.test_app import GAMES


@override_settings(ALLOWED_HOSTS=["localhost", "testserver"], Q_CLUSTER={"sync": True})
class BrowserSmoke(StaticLiveServerTestCase):
    def test_broken_art_uses_steam_fallback(self) -> None:
        from playwright.sync_api import sync_playwright

        user = get_user_model().objects.create_user("art-viewer")
        self.client.force_login(user)
        GameIdentity.objects.create(
            game=Game.objects.create(title="Forza Horizon 6"),
            provider="steam",
            external_id="2483190",
        )
        run = RecommendationRun.objects.create(
            user=user,
            inputs={},
            results=[GAMES[0] | {"title": "Forza Horizon 6", "category": "discover"}],
        )
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            context = browser.new_context()
            context.add_cookies(
                [
                    {
                        "name": settings.SESSION_COOKIE_NAME,
                        "value": self.client.cookies[
                            settings.SESSION_COOKIE_NAME
                        ].value,
                        "url": self.live_server_url,
                    }
                ]
            )
            page = context.new_page()
            page.route("**/2483190/header.jpg", lambda route: route.fulfill(status=404))
            page.route(
                "**/games/art/2483190/",
                lambda route: route.fulfill(
                    status=200,
                    content_type="image/svg+xml",
                    body=(
                        '<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1"/>'
                    ),
                ),
            )
            page.goto(self.live_server_url + f"/runs/{run.pk}/")
            page.wait_for_function(
                "document.querySelector('.recommendation-art')?.naturalWidth > 0",
                timeout=5000,
            )
            self.assertEqual(
                page.locator(".recommendation-art").get_attribute("src"),
                "/games/art/2483190/",
            )
            browser.close()

    def test_taste_to_direct_ai_request_and_feedback(self) -> None:
        from playwright.sync_api import sync_playwright

        with (
            sync_playwright() as playwright,
            patch("app.recommendations.ask_provider", return_value=GAMES) as ask,
        ):
            browser = playwright.chromium.launch()
            page = browser.new_page(viewport={"width": 1280, "height": 900})
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(self.live_server_url)
            page.screenshot(
                path="/tmp/game-recommender-landing-desktop.png", full_page=True
            )
            page.set_viewport_size({"width": 390, "height": 844})
            self.assertTrue(
                page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
            )
            page.screenshot(
                path="/tmp/game-recommender-landing-mobile.png", full_page=True
            )
            page.get_by_role("link", name="Get started", exact=True).first.click()
            self.assertTrue(
                page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
            )
            page.set_viewport_size({"width": 1280, "height": 900})
            page.get_by_label("Username").fill("browser-player")
            page.get_by_label("Password:", exact=True).fill("browser-smoke-password-82")
            page.get_by_label("Password confirmation").fill("browser-smoke-password-82")
            page.get_by_role("button", name="Create account").click()
            page.get_by_role("link", name="Find a game").click()
            page.get_by_label("Game title").fill("Portal 2")
            page.get_by_role("button", name="Search games").click()
            page.get_by_role("link", name='Add "Portal 2" yourself').click()
            page.get_by_label("Game:", exact=True).fill("Portal 2")
            page.get_by_label("How did you feel about it?", exact=False).select_option(
                "1"
            )
            page.get_by_label("Why?", exact=True).fill("Clever puzzles")
            page.get_by_role("button", name="Save game").click()
            page.get_by_role("button", name="Find games").wait_for()
            page.get_by_label("Anything you want this time?", exact=False).fill(
                "Something relaxing"
            )
            with page.expect_response(
                lambda response: response.url.endswith("/recommend/")
            ) as submitted:
                page.get_by_role("button", name="Find games").click()
            self.assertEqual(submitted.value.status, 200, submitted.value.text()[:1000])
            self.assertIn("The Talos Principle", submitted.value.text())
            self.assertEqual(errors, [])
            page.locator(".card h3").wait_for(timeout=5000)
            self.assertEqual(
                page.locator(".card h3").all_text_contents(), ["The Talos Principle"]
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
            self.assertEqual(
                page.url.rstrip("/"), self.live_server_url
            )  # HTMX updated the page directly.
            page.screenshot(
                path="/tmp/game-recommender-simple-desktop.png", full_page=True
            )
            page.set_viewport_size({"width": 390, "height": 844})
            self.assertTrue(
                page.evaluate(
                    "document.documentElement.scrollWidth <= window.innerWidth"
                )
            )
            page.screenshot(
                path="/tmp/game-recommender-simple-mobile.png", full_page=True
            )
            page.get_by_role("link", name="Disliked it", exact=True).click()
            page.get_by_role("link", name='Add "The Talos Principle" yourself').click()
            self.assertEqual(
                page.get_by_label("Game:", exact=True).input_value(),
                "The Talos Principle",
            )
            page.get_by_label("Why?", exact=True).fill("Too slow for me")
            page.get_by_role("button", name="Save game").click()
            page.get_by_role("link", name="Library", exact=True).click()
            page.get_by_text("Too slow for me").wait_for()
            self.assertEqual(errors, [])
            browser.close()
