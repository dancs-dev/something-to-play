from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

CONFIDENCE = [MinValueValidator(0), MaxValueValidator(1)]
PLATFORMS = [(x, x.title()) for x in ('windows', 'macos', 'linux')]
PLAY_MODES = [('any', 'Any'), ('solo', 'Solo'), ('coop', 'Co-operative'), ('competitive', 'Competitive')]
SCOPES = [('owned', 'Owned games'), ('new', 'New to my library'), ('either', 'Either')]
LEVELS = [(1, 'Low'), (2, 'Medium'), (3, 'High')]
ATTRIBUTES = ('mechanics', 'themes', 'platforms', 'solo', 'coop', 'competitive',
              'session_minutes', 'attention', 'intensity', 'pause_flexible', 'learning_effort')


class Suitability(models.Model):
    session_minutes = models.PositiveIntegerField(null=True, blank=True)
    attention = models.PositiveSmallIntegerField(choices=LEVELS, null=True, blank=True)
    intensity = models.PositiveSmallIntegerField(choices=LEVELS, null=True, blank=True)
    pause_flexible = models.BooleanField(null=True, blank=True)
    learning_effort = models.PositiveSmallIntegerField(choices=LEVELS, null=True, blank=True)
    solo = models.BooleanField(null=True, blank=True)
    coop = models.BooleanField(null=True, blank=True)
    competitive = models.BooleanField(null=True, blank=True)

    class Meta:
        abstract = True


class Game(Suitability):
    title = models.CharField(max_length=200)
    steam_appid = models.PositiveIntegerField(unique=True, null=True, blank=True)
    platforms = models.JSONField(default=list)
    mechanics = models.JSONField(default=list)
    themes = models.JSONField(default=list)
    demonstration = models.BooleanField(default=False)
    source_url = models.URLField(blank=True)

    class Meta:
        ordering = ['title', 'pk']

    def __str__(self):
        return self.title


class GameMode(Suitability):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='modes')
    name = models.CharField(max_length=100)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['game', 'name'], name='unique_game_mode')]

    def __str__(self):
        return f'{self.game}: {self.name}'


class GameEvidence(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='evidence')
    mode = models.ForeignKey(GameMode, on_delete=models.CASCADE, null=True, blank=True)
    attribute = models.CharField(max_length=40, choices=[(x, x) for x in ATTRIBUTES])
    value = models.JSONField(null=True, blank=True)
    source = models.CharField(max_length=40)
    source_url = models.URLField(blank=True)
    excerpt = models.TextField(blank=True)
    confidence = models.FloatField(default=0.5, validators=CONFIDENCE)
    verified = models.BooleanField(default=False)
    observed_at = models.DateTimeField(auto_now_add=True)


class SteamAccount(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='steam_account')
    steam_id = models.CharField(max_length=20, unique=True)
    sync_requested = models.BooleanField(default=True)
    status = models.CharField(max_length=20, default='pending')
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    error = models.CharField(max_length=250, blank=True)


class OpenIDNonce(models.Model):
    value = models.CharField(max_length=255, unique=True)
    used_at = models.DateTimeField(auto_now_add=True)


class OwnershipActivity(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    manual_owned = models.BooleanField(null=True, blank=True)
    steam_owned = models.BooleanField(null=True, blank=True)
    total_minutes = models.PositiveIntegerField(null=True, blank=True)
    recent_minutes = models.PositiveIntegerField(null=True, blank=True)
    observed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'game'], name='unique_user_game')]

    @property
    def owned(self):
        return self.manual_owned if self.manual_owned is not None else self.steam_owned is True


class PlaytimeSnapshot(models.Model):
    activity = models.ForeignKey(OwnershipActivity, on_delete=models.CASCADE, related_name='snapshots')
    observed_at = models.DateTimeField()
    total_minutes = models.PositiveIntegerField()
    recent_minutes = models.PositiveIntegerField(null=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['activity', 'observed_at'], name='unique_playtime_observation')]


class Preference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.SET_NULL, null=True, blank=True)
    subject = models.CharField(max_length=200, help_text='Game title, mechanic or theme')
    kind = models.CharField(max_length=20, choices=[('game', 'Game'), ('mechanic', 'Mechanic'), ('theme', 'Theme')], default='game')
    sentiment = models.SmallIntegerField(choices=[(1, 'Like'), (-1, 'Dislike')])
    reason = models.TextField(blank=True, max_length=2000)
    source = models.CharField(max_length=30, default='manual')
    confidence = models.FloatField(default=1, validators=CONFIDENCE)
    explicit = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'kind', 'subject'], name='unique_preference_subject'),
                       models.CheckConstraint(condition=Q(confidence__gte=0, confidence__lte=1), name='preference_confidence_range')]


class ConversationTurn(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    text = models.TextField(max_length=4000)
    created_at = models.DateTimeField(auto_now_add=True)
    status = models.CharField(max_length=20, default='manual')
    proposals = models.JSONField(default=list)
    prompt_version = models.CharField(max_length=30, blank=True)


class ReviewEvidence(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='reviews')
    external_id = models.CharField(max_length=40, unique=True)
    excerpt = models.TextField()
    positive = models.BooleanField()
    source_url = models.URLField()
    observed_at = models.DateTimeField(auto_now=True)


class RecommendationRun(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    inputs = models.JSONField()
    scoring_version = models.CharField(max_length=30, default='scoring-v1')
    scores = models.JSONField(default=list)
    results = models.JSONField(default=list)
    deterministic_results = models.JSONField(default=list)
    status = models.CharField(max_length=20, default='deterministic')
    lease_until = models.DateTimeField(null=True, blank=True)
    diagnostics = models.JSONField(default=dict)


class Feedback(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    run = models.ForeignKey(RecommendationRun, on_delete=models.CASCADE, related_name='feedback')
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    kind = models.CharField(max_length=20, choices=[('like', 'Good fit'), ('not_tonight', 'Not tonight'), ('dislike', 'I dislike this game')])
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'run', 'game'], name='unique_run_feedback')]
