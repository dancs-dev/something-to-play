from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from app import accounts, views

urlpatterns = [
    path('admin/', admin.site.urls),
    path('', views.home, name='home'),
    path('signup/', accounts.signup, name='signup'),
    path('login/', auth_views.LoginView.as_view(), name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('profile/', views.profile, name='profile'),
    path('onboarding/', views.profile, name='onboarding'),
    path('preferences/new/', views.preference, name='preference_new'),
    path('preferences/<int:pk>/', views.preference, name='preference_edit'),
    path('preferences/<int:pk>/delete/', views.delete_preference, name='preference_delete'),
    path('recommend/', views.recommend, name='recommend'),
    path('runs/', views.history, name='history'),
    path('runs/<int:pk>/', views.run_detail, name='run'),
]
