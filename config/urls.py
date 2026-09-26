from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from app import accounts, views, steam

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('signup/', accounts.signup, name='signup'),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('onboarding/', views.onboarding, name='onboarding'),
    path('profile/', views.profile, name='profile'),
    path('preferences/new/', views.preference, name='preference_new'),
    path('preferences/<int:pk>/', views.preference, name='preference_edit'),
    path('preferences/<int:pk>/delete/', views.delete_preference, name='preference_delete'),
    path('ownership/', views.ownership, name='ownership'),
    path('recommend/', views.recommend, name='recommend'),
    path('runs/', views.history, name='history'),
    path('runs/<int:pk>/', views.run_detail, name='run'),
    path('runs/<int:pk>/feedback/', views.feedback, name='feedback'),
    path('conversation/', views.conversation, name='conversation'),
    path('conversation/<int:pk>/<int:index>/confirm/', views.confirm_proposal, name='confirm_proposal'),
    path('steam/start/', steam.start, name='steam_start'),
    path('steam/callback/', steam.callback, name='steam_callback'),
    path('steam/sync/', views.request_sync, name='steam_sync'),
]
