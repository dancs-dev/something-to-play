from django.contrib import admin

from .models import Preference, RecommendationRun, SavedSuggestion

admin.site.register(Preference)
admin.site.register(RecommendationRun)
admin.site.register(SavedSuggestion)
