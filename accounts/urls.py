"""MCS Platform - Accounts URL Configuration"""
from django.urls import path
from django.contrib.auth import views as auth_views
from . import views

app_name = "accounts"

urlpatterns = [
    # Login / Logout
    path("login/", views.MCSLoginView.as_view(), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("totp-verify/", views.totp_verify_view, name="totp_verify"),

    # Invitation-based signup (public — no login required)
    path("signup/<str:token>/", views.invitation_signup_view, name="invitation_signup"),

    # Profile
    path("profile/", views.profile_view, name="profile"),

    # User management (admin only)
    path("users/", views.user_list, name="user_list"),
    path("users/create/", views.user_create, name="user_create"),
    path("users/<uuid:pk>/edit/", views.user_edit, name="user_edit"),
    path("users/<uuid:pk>/reset-2fa/", views.user_reset_2fa, name="user_reset_2fa"),

    # Invitation management (admin only)
    path("invitations/", views.invitation_list, name="invitation_list"),
    path("invitations/create/", views.invitation_create, name="invitation_create"),
    path("invitations/<uuid:pk>/resend/", views.invitation_resend, name="invitation_resend"),
    path("invitations/<uuid:pk>/revoke/", views.invitation_revoke, name="invitation_revoke"),
    path("invitations/<uuid:pk>/delete/", views.invitation_delete, name="invitation_delete"),

    # 2FA setup
    path("setup-2fa/", views.setup_2fa_view, name="setup_2fa"),
]
