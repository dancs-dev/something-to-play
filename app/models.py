from django.conf import settings
from django.db import models


class Preference(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    subject = models.CharField('game', max_length=200)
    sentiment = models.SmallIntegerField(choices=[(1, 'Like'), (-1, 'Dislike')])
    reason = models.TextField('why', blank=True, max_length=2000)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [models.UniqueConstraint(fields=['user', 'subject'], name='unique_user_taste')]

    def __str__(self):
        return f'{self.subject}: {self.get_sentiment_display()}'


class RecommendationRun(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    inputs = models.JSONField()
    results = models.JSONField(default=list)
