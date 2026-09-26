from django.contrib import admin
from .models import (Game, GameMode, GameEvidence, SteamAccount, OwnershipActivity,
                     PlaytimeSnapshot, Preference, ConversationTurn, ReviewEvidence,
                     RecommendationRun, Feedback)

@admin.register(Game)
class GameAdmin(admin.ModelAdmin):
    list_display = ('title', 'steam_appid', 'demonstration')
    search_fields = ('title',)
    list_filter = ('demonstration',)

for model in (GameMode, GameEvidence, SteamAccount, OwnershipActivity, PlaytimeSnapshot,
              Preference, ConversationTurn, ReviewEvidence, RecommendationRun, Feedback):
    admin.site.register(model)
