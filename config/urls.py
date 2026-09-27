from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from app import accounts, views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("", views.HomeView.as_view(), name="home"),
    path("signup/", accounts.SignupView.as_view(), name="signup"),
    path("login/", auth_views.LoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("onboarding/", views.ProfileView.as_view(), name="onboarding"),
    path(
        "preferences/new/", views.PreferenceCreateView.as_view(), name="preference_new"
    ),
    path(
        "preferences/<int:pk>/",
        views.PreferenceUpdateView.as_view(),
        name="preference_edit",
    ),
    path(
        "preferences/<int:pk>/delete/",
        views.PreferenceDeleteView.as_view(),
        name="preference_delete",
    ),
    path("games/search/", views.GameSearchView.as_view(), name="game_search"),
    path(
        "games/catalogue/refresh/",
        views.CatalogueRefreshView.as_view(),
        name="catalogue_refresh",
    ),
    path("library/", views.LibraryView.as_view(), name="library"),
    path("settings/", views.SettingsView.as_view(), name="settings"),
    path("library/link/", views.SteamLinkView.as_view(), name="steam_link"),
    path("library/sync/", views.SteamSyncView.as_view(), name="steam_sync"),
    path(
        "library/games/<int:game_id>/curate/",
        views.LibraryCurationView.as_view(),
        name="library_curate",
    ),
    path("recommend/", views.RecommendationView.as_view(), name="recommend"),
    path("runs/", views.HistoryView.as_view(), name="history"),
    path("runs/<int:pk>/", views.RunDetailView.as_view(), name="run"),
]
