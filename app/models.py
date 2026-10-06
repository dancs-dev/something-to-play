import re
from collections import defaultdict
from collections.abc import Iterable
from typing import TYPE_CHECKING

from django.conf import settings
from django.db import models
from django.db.models import Prefetch, Q
from django.db.models.base import ModelBase
from django.utils import timezone


def normalize_title(title: str) -> str:
    return " ".join(re.findall(r"\w+", title.casefold()))


class Game(models.Model):
    title = models.CharField(max_length=200)
    normalized_title = models.TextField(db_index=True, default="", editable=False)

    def __str__(self) -> str:
        return self.title

    def save(
        self,
        *,
        force_insert: bool | tuple[ModelBase, ...] = False,
        force_update: bool = False,
        using: str | None = None,
        update_fields: Iterable[str] | None = None,
    ) -> None:
        self.normalized_title = normalize_title(self.title)
        if update_fields is not None:
            fields = set(update_fields)
            if "title" in fields:
                fields.add("normalized_title")
            update_fields = fields
        super().save(
            force_insert=force_insert,
            force_update=force_update,
            using=using,
            update_fields=update_fields,
        )


class GameIdentity(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name="identities")
    provider = models.CharField(max_length=30)
    external_id = models.CharField(max_length=64)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["provider", "external_id"], name="unique_game_identity"
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.external_id}"


class LinkedAccount(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    provider = models.CharField(max_length=30)
    external_user_id = models.CharField(max_length=64)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "provider"], name="unique_user_provider"
            )
        ]

    def __str__(self) -> str:
        return f"{self.provider}:{self.external_user_id}"


class Ownership(models.Model):
    account = models.ForeignKey(LinkedAccount, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["account", "game"], name="unique_account_game"
            )
        ]

    def __str__(self) -> str:
        return f"{self.account}: {self.game}"


class CatalogueState(models.Model):
    provider = models.CharField(max_length=30, unique=True)
    last_synced_at = models.DateTimeField()

    def __str__(self) -> str:
        return self.provider


class Preference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    sentiment = models.SmallIntegerField(
        choices=[
            (2, "Loved"),
            (1, "Like"),
            (-1, "Dislike"),
            (0, "Ignore"),
            (-2, "Not played yet"),
        ]
    )
    reason = models.TextField("why", blank=True, max_length=2000)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.UniqueConstraint(fields=["user", "game"], name="unique_user_taste")
        ]

    if TYPE_CHECKING:

        def get_sentiment_display(self) -> str: ...

    def __str__(self) -> str:
        return f"{self.game.title}: {self.get_sentiment_display()}"


def _steam_appids_for_titles(titles: list[str]) -> dict[str, list[str]]:
    targets = {
        title: normalize_title(re.sub(r"\s+\((?:19|20)\d{2}\)$", "", title))
        for title in titles
    }
    keys = {key for key in targets.values() if key}
    if not keys:
        return {title: [] for title in titles}
    lookup = Q(normalized_title__in=keys)
    for key in keys:
        if " " in key:
            lookup |= Q(normalized_title__gte=key + " ", normalized_title__lt=key + "!")
    candidates: dict[str, list[str]] = defaultdict(list)
    games = (
        Game.objects.filter(lookup)
        .order_by("pk")
        .prefetch_related(
            Prefetch(
                "identities",
                queryset=GameIdentity.objects.filter(provider="steam").order_by("pk"),
            )
        )
    )
    for game in games:
        candidates[game.normalized_title].extend(
            identity.external_id for identity in game.identities.all()
        )
    matches = {}
    for title, target in targets.items():
        exact = candidates.get(target, [])
        if exact:
            matches[title] = exact
            continue
        # ponytail: one-word edition searches are too broad; add aliases if needed.
        matches[title] = [
            appid
            for name, appids in candidates.items()
            if " " in target
            and name.startswith(target + " ")
            and re.search(
                r"\b(?:edition|cut|complete|definitive|ultimate|deluxe)\b",
                name[len(target) + 1 :],
            )
            for appid in appids
        ]
    return matches


class RecommendationRun(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        DONE = "done", "Done"
        FAILED = "failed", "Failed"
        CANCELLED = "cancelled", "Cancelled"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    inputs = models.JSONField()
    results = models.JSONField(default=list)
    # DONE is the default so runs created directly (admin, scripts, older code)
    # are treated as finished; the async path sets PENDING explicitly.
    status = models.CharField(max_length=9, choices=Status.choices, default=Status.DONE)
    error = models.TextField(blank=True, default="")

    def __str__(self) -> str:
        return f"Recommendation run {self.pk} for {self.user}"

    @property
    def is_pending(self) -> bool:
        return self.status in {self.Status.PENDING, self.Status.RUNNING}

    @property
    def elapsed_seconds(self) -> int:
        return int((timezone.now() - self.created_at).total_seconds())

    # Wait-state copy keyed off elapsed seconds. Rendered into the page as
    # JSON so the client timer advances the copy from the same thresholds.
    STAGES = [
        (0, "Reading your reasons"),
        (5, "Weighing what you loved"),
        (12, "Checking your backlog"),
        (22, "Writing your picks"),
        (60, "Still writing. Good picks take longer to write."),
    ]

    @property
    def stage(self) -> str:
        seconds = self.elapsed_seconds
        label = self.STAGES[0][1]
        for at, text in self.STAGES:
            if seconds >= at:
                label = text
        return label

    @property
    def illustrated_results(self) -> list[dict[str, str | bool]]:
        results = sorted(
            self.results,
            key=lambda result: {"discover": 0, "backlog": 1, "replay": 2}.get(
                result.get("category"), 3
            ),
        )
        matches = _steam_appids_for_titles(
            [result.get("title", "") for result in results]
        )
        ambiguous = {
            appid
            for result in results
            if result.get("category") in {"replay", "backlog"}
            for appids in [matches[result.get("title", "")]]
            if len(appids) > 1
            for appid in appids
        }
        owned = (
            set(
                GameIdentity.objects.filter(
                    provider="steam",
                    external_id__in=ambiguous,
                    game__ownership__account__user=self.user,
                    game__ownership__is_active=True,
                ).values_list("external_id", flat=True)
            )
            if ambiguous
            else set()
        )
        dismissed = (
            set(
                DismissedSuggestion.objects.filter(user=self.user).values_list(
                    "game__normalized_title", flat=True
                )
            )
            if any(result.get("category") == "discover" for result in results)
            else set()
        )
        illustrated = []
        for result in results:
            title = result.get("title", "")
            appids = matches[title]
            if len(appids) > 1 and result.get("category") in {"replay", "backlog"}:
                appids = [appid for appid in appids if appid in owned] or appids
            appid = appids[0] if appids else ""
            valid_appid = (
                appid if appid and appid.isascii() and appid.isdecimal() else ""
            )
            image_url = (
                f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{valid_appid}/header.jpg"
                if valid_appid
                else ""
            )
            illustrated.append(
                result
                | {
                    "image_url": image_url,
                    "steam_appid": valid_appid,
                    "dismissed": normalize_title(title) in dismissed,
                }
            )
        return illustrated


class DismissedSuggestion(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.CASCADE)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "game"], name="unique_user_dismissed_game"
            )
        ]

    def __str__(self) -> str:
        return f"{self.user}: {self.game}"
