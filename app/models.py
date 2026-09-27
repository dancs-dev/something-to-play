from django.conf import settings
from django.db import models


class Game(models.Model):
    title = models.CharField(max_length=200)

    def __str__(self):
        return self.title


class GameIdentity(models.Model):
    game = models.ForeignKey(Game, on_delete=models.CASCADE, related_name='identities')
    provider = models.CharField(max_length=30)
    external_id = models.CharField(max_length=64)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['provider', 'external_id'], name='unique_game_identity')]


class LinkedAccount(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    provider = models.CharField(max_length=30)
    external_user_id = models.CharField(max_length=64)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'provider'], name='unique_user_provider')]


class Ownership(models.Model):
    account = models.ForeignKey(LinkedAccount, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['account', 'game'], name='unique_account_game')]


class CatalogueState(models.Model):
    provider = models.CharField(max_length=30, unique=True)
    last_synced_at = models.DateTimeField()


class Preference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    game = models.ForeignKey(Game, on_delete=models.CASCADE)
    sentiment = models.SmallIntegerField(choices=[(2, 'Loved'), (1, 'Like'), (-1, 'Dislike'), (0, 'Ignore'), (-2, 'Not played yet')])
    reason = models.TextField('why', blank=True, max_length=2000)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [models.UniqueConstraint(fields=['user', 'game'], name='unique_user_taste')]

    def __str__(self):
        return f'{self.game.title}: {self.get_sentiment_display()}'


class RecommendationRun(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    inputs = models.JSONField()
    results = models.JSONField(default=list)
