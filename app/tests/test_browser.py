"""Optional: uv run --with playwright python manage.py test app.tests.test_browser."""

from importlib.util import find_spec
from unittest import skipUnless
from unittest.mock import patch

from django.contrib.staticfiles.testing import StaticLiveServerTestCase
from django.test import override_settings

from app.tests.test_app import GAMES


@skipUnless(
    find_spec("playwright"), "Optional browser check: run with uv run --with playwright"
)
@override_settings(ALLOWED_HOSTS=["localhost", "testserver"])
class BrowserSmoke(StaticLiveServerTestCase):
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
            page.get_by_role("link", name="Add “Portal 2” yourself").click()
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
                owned={"replay": [], "backlog": [], "all": [], "known": ["Portal 2"]},
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
            page.get_by_role("link", name="Add “The Talos Principle” yourself").click()
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
