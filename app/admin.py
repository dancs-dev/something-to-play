from django.contrib import admin
from .models import Preference, RecommendationRun

admin.site.register(Preference)
admin.site.register(RecommendationRun)
