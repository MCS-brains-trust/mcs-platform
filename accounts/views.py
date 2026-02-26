"""MCS Platform - Account Views with Invitation-Based Signup and TOTP 2FA"""
import io
import base64
import logging
import threading
import pyotp
import qrcode
from django.conf import settings
from django.contrib.auth import views as auth_views, login as auth_login
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.shortcuts import render, redirect, get_object_or_404
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.utils.html import strip_tags
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from .models import User, Invitation, _default_token
from .forms import (
    MCSLoginForm,
    TOTPVerifyForm,
    InvitationForm,
    InvitationSignupForm,
    UserCreateForm,
    UserEditForm,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Login with 2FA
# ---------------------------------------------------------------------------

@method_decorator(ratelimit(key='ip', rate='5/m', method='POST'), name='dispatch')
class MCSLoginView(auth_views.LoginView):
    """
    Step 1 of login: username + password.
    If the user has 2FA enabled, store their pk in session and redirect to TOTP verify.
    If not, log them in directly.
    """
    template_name = "accounts/login.html"
    authentication_form = MCSLoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        user = form.get_user()
        if user.has_2fa:
            # Store user pk in session for 2FA step, don't log in yet
            self.request.session["2fa_user_pk"] = str(user.pk)
            self.request.session["2fa_next"] = self.request.POST.get("next", "")
            return redirect("accounts:totp_verify")
        # No 2FA — log in directly
        return super().form_valid(form)


@ratelimit(key='ip', rate='5/m', method='POST')
def totp_verify_view(request):
    """
    Step 2 of login: TOTP verification.
    Only accessible if the user passed step 1 (username + password).
    """
    user_pk = request.session.get("2fa_user_pk")
    if not user_pk:
        return redirect("accounts:login")

    user = get_object_or_404(User, pk=user_pk)

    if request.method == "POST":
        form = TOTPVerifyForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data["totp_code"]
            totp = pyotp.TOTP(user.totp_secret)
            if totp.verify(code, valid_window=1):
                # Code valid — complete login
                del request.session["2fa_user_pk"]
                next_url = request.session.pop("2fa_next", "")
                # Cycle session key to prevent session fixation
                request.session.cycle_key()
                auth_login(request, user)
                return redirect(next_url or settings.LOGIN_REDIRECT_URL)
            else:
                form.add_error("totp_code", "Invalid code. Please try again.")
    else:
        form = TOTPVerifyForm()

    return render(request, "accounts/totp_verify.html", {
        "form": form,
        "user_name": user.get_full_name() or user.username,
    })


# ---------------------------------------------------------------------------
# Invitation Management (Admin only)
# ---------------------------------------------------------------------------

@login_required
def invitation_list(request):
    """List all invitations. Admin only."""
    if not request.user.is_admin:
        messages.error(request, "You do not have permission to manage invitations.")
        return redirect("review:dashboard")

    # Auto-expire old invitations
    Invitation.objects.filter(
        status=Invitation.Status.PENDING,
        expires_at__lte=timezone.now(),
    ).update(status=Invitation.Status.EXPIRED)

    invitations = Invitation.objects.all()
    return render(request, "accounts/invitation_list.html", {"invitations": invitations})


@login_required
def invitation_create(request):
    """Create and send a new invitation. Admin only."""
    if not request.user.is_admin:
        messages.error(request, "You do not have permission to send invitations.")
        return redirect("review:dashboard")

    # Check user limit (7 users max)
    active_users = User.objects.filter(is_active=True).count()
    pending_invitations = Invitation.objects.filter(status=Invitation.Status.PENDING).count()
    if active_users + pending_invitations >= 7:
        messages.error(request, "Maximum of 7 users reached. Deactivate a user or revoke a pending invitation first.")
        return redirect("accounts:invitation_list")

    if request.method == "POST":
        form = InvitationForm(request.POST)
        if form.is_valid():
            invitation = form.save(commit=False)
            invitation.invited_by = request.user
            invitation.save()

            # Send invitation email (non-blocking)
            _send_invitation_email(request, invitation)

            messages.success(
                request,
                f"Invitation created for {invitation.first_name} {invitation.last_name} "
                f"({invitation.email}). The email is being sent — check the status on this page."
            )
            return redirect("accounts:invitation_list")
    else:
        form = InvitationForm()

    return render(request, "accounts/invitation_form.html", {
        "form": form,
        "title": "Send Invitation",
    })


@login_required
@require_POST
def invitation_resend(request, pk):
    """Resend an invitation email. Admin only."""
    if not request.user.is_admin:
        messages.error(request, "You do not have permission.")
        return redirect("review:dashboard")

    invitation = get_object_or_404(Invitation, pk=pk)
    if invitation.status in (Invitation.Status.EXPIRED, Invitation.Status.REVOKED):
        # Reactivate: new token, reset expiry
        invitation.token = _default_token()
        invitation.expires_at = timezone.now() + timezone.timedelta(days=7)
        invitation.status = Invitation.Status.PENDING
        invitation.email_error = ""
        invitation.email_sent_at = None
        invitation.save(update_fields=["token", "expires_at", "status", "email_error", "email_sent_at"])
    elif invitation.status == Invitation.Status.PENDING:
        # Clear previous email status before resending
        invitation.email_error = ""
        invitation.email_sent_at = None
        invitation.save(update_fields=["email_error", "email_sent_at"])

    _send_invitation_email(request, invitation)
    messages.success(request, f"Invitation is being resent to {invitation.email}.")
    return redirect("accounts:invitation_list")


@login_required
@require_POST
def invitation_revoke(request, pk):
    """Revoke a pending invitation. Admin only."""
    if not request.user.is_admin:
        messages.error(request, "You do not have permission.")
        return redirect("review:dashboard")

    invitation = get_object_or_404(Invitation, pk=pk)
    if invitation.status == Invitation.Status.PENDING:
        invitation.status = Invitation.Status.REVOKED
        invitation.save(update_fields=["status"])
        messages.success(request, f"Invitation for {invitation.email} revoked.")
    return redirect("accounts:invitation_list")


def _send_invitation_email(request, invitation):
    """Send the invitation email with signup link in a background thread.

    Renders the email template synchronously (needs request context), then
    dispatches the actual SMTP send to a daemon thread so the admin isn't
    left waiting on a slow mail server.  Delivery status is persisted on the
    Invitation row so it can be checked from the list page later.
    """
    signup_url = request.build_absolute_uri(f"/accounts/signup/{invitation.token}/")

    subject = "You're invited to StatementHub — MC & S Pty Ltd"
    html_message = render_to_string("accounts/email_invitation.html", {
        "invitation": invitation,
        "signup_url": signup_url,
    })
    plain_message = strip_tags(html_message)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@statementhub.com.au")
    invitation_pk = invitation.pk

    def _send():
        from accounts.models import Invitation as InvModel
        try:
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=from_email,
                recipient_list=[invitation.email],
                html_message=html_message,
                fail_silently=False,
            )
            InvModel.objects.filter(pk=invitation_pk).update(
                email_sent_at=timezone.now(),
                email_error="",
            )
            logger.info("Invitation email sent (invitation_id=%s)", invitation_pk)
        except Exception as e:
            logger.error("Failed to send invitation email (invitation_id=%s): %s", invitation_pk, e)
            InvModel.objects.filter(pk=invitation_pk).update(
                email_error=str(e)[:500],
            )

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()


# ---------------------------------------------------------------------------
# Invitation Signup (Public — no login required)
# ---------------------------------------------------------------------------

def invitation_signup_view(request, token):
    """
    Accept an invitation: set username, password, and configure TOTP 2FA.
    """
    invitation = get_object_or_404(Invitation, token=token)

    if not invitation.is_valid:
        return render(request, "accounts/invitation_invalid.html", {
            "invitation": invitation,
        })

    # Generate a TOTP secret for this signup session
    if "signup_totp_secret" not in request.session:
        request.session["signup_totp_secret"] = pyotp.random_base32()

    totp_secret = request.session["signup_totp_secret"]
    totp = pyotp.TOTP(totp_secret)
    provisioning_uri = totp.provisioning_uri(
        name=invitation.email,
        issuer_name="StatementHub",
    )

    # Generate QR code as base64 image
    qr_img = qrcode.make(provisioning_uri, box_size=6, border=2)
    buffer = io.BytesIO()
    qr_img.save(buffer, format="PNG")
    qr_base64 = base64.b64encode(buffer.getvalue()).decode()

    if request.method == "POST":
        form = InvitationSignupForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data["totp_code"]
            if totp.verify(code, valid_window=1):
                # Create the user
                user = User.objects.create_user(
                    username=form.cleaned_data["username"],
                    email=invitation.email,
                    password=form.cleaned_data["password1"],
                    first_name=invitation.first_name,
                    last_name=invitation.last_name,
                    role=invitation.role,
                    totp_secret=totp_secret,
                    totp_confirmed=True,
                )

                # Mark invitation as accepted
                invitation.status = Invitation.Status.ACCEPTED
                invitation.accepted_at = timezone.now()
                invitation.created_user = user
                invitation.save()

                # Clean up session
                del request.session["signup_totp_secret"]

                # Log the user in
                auth_login(request, user)
                messages.success(
                    request,
                    f"Welcome to StatementHub, {user.first_name}! Your account is set up with two-factor authentication."
                )
                return redirect(settings.LOGIN_REDIRECT_URL)
            else:
                form.add_error("totp_code", "Invalid authenticator code. Please scan the QR code and try again.")
    else:
        # Pre-fill suggested username from email
        suggested_username = invitation.email.split("@")[0].lower().replace(".", "_")
        form = InvitationSignupForm(initial={"username": suggested_username})

    return render(request, "accounts/invitation_signup.html", {
        "form": form,
        "invitation": invitation,
        "qr_base64": qr_base64,
        "totp_secret": totp_secret,
    })


# ---------------------------------------------------------------------------
# User Management (Admin only)
# ---------------------------------------------------------------------------

@login_required
def profile_view(request):
    from core.models import AuditLog

    context = {}

    if request.method == "POST":
        form_type = request.POST.get("form_type")

        if form_type == "profile":
            request.user.first_name = request.POST.get("first_name", "").strip()
            request.user.last_name = request.POST.get("last_name", "").strip()
            request.user.email = request.POST.get("email", "").strip()
            request.user.phone = request.POST.get("phone", "").strip()
            request.user.save(update_fields=["first_name", "last_name", "email", "phone"])
            context["profile_updated"] = True

        elif form_type == "password":
            current_password = request.POST.get("current_password", "")
            new_password1 = request.POST.get("new_password1", "")
            new_password2 = request.POST.get("new_password2", "")
            errors = []

            if not request.user.check_password(current_password):
                errors.append("Current password is incorrect.")
            elif new_password1 != new_password2:
                errors.append("New passwords do not match.")
            elif len(new_password1) < 8:
                errors.append("New password must be at least 8 characters.")
            else:
                try:
                    validate_password(new_password1, request.user)
                except DjangoValidationError as e:
                    errors.extend(e.messages)

            if errors:
                context["password_errors"] = errors
            else:
                request.user.set_password(new_password1)
                request.user.save()
                # Re-authenticate so the user isn't logged out
                from django.contrib.auth import update_session_auth_hash
                update_session_auth_hash(request, request.user)
                context["password_changed"] = True

    # Activity history — last 5 audit log entries for this user
    context["audit_logs"] = AuditLog.objects.filter(user=request.user)[:5]

    return render(request, "accounts/profile.html", context)


@login_required
def user_list(request):
    if not request.user.is_admin:
        messages.error(request, "You do not have permission to manage users.")
        return redirect("review:dashboard")
    users = User.objects.all()
    invitations = Invitation.objects.filter(status=Invitation.Status.PENDING)
    return render(request, "accounts/user_list.html", {
        "users": users,
        "invitations": invitations,
    })


@login_required
def user_create(request):
    if not request.user.is_admin:
        messages.error(request, "You do not have permission to create users.")
        return redirect("review:dashboard")
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            user = form.save()
            messages.success(request, f"User {user.username} created successfully.")
            return redirect("accounts:user_list")
    else:
        form = UserCreateForm()
    return render(request, "accounts/user_form.html", {"form": form, "title": "Create User"})


@login_required
def user_edit(request, pk):
    if not request.user.is_admin:
        messages.error(request, "You do not have permission to edit users.")
        return redirect("review:dashboard")
    user = get_object_or_404(User, pk=pk)
    if request.method == "POST":
        form = UserEditForm(request.POST, instance=user)
        if form.is_valid():
            form.save()
            messages.success(request, f"User {user.username} updated successfully.")
            return redirect("accounts:user_list")
    else:
        form = UserEditForm(instance=user)
    return render(request, "accounts/user_form.html", {"form": form, "title": f"Edit User: {user.username}"})


@login_required
def user_reset_2fa(request, pk):
    """Reset a user's TOTP 2FA. Admin only. User will need to re-setup on next login."""
    if not request.user.is_admin:
        messages.error(request, "You do not have permission.")
        return redirect("review:dashboard")
    user = get_object_or_404(User, pk=pk)
    user.totp_secret = ""
    user.totp_confirmed = False
    user.save(update_fields=["totp_secret", "totp_confirmed"])
    messages.success(request, f"2FA reset for {user.get_full_name() or user.username}. They will need to set up 2FA again.")
    return redirect("accounts:user_list")


@login_required
def setup_2fa_view(request):
    """Mandatory 2FA setup for users without TOTP configured."""
    user = request.user
    if user.has_2fa:
        return redirect(settings.LOGIN_REDIRECT_URL)

    # Generate or reuse TOTP secret
    if "setup_totp_secret" not in request.session:
        request.session["setup_totp_secret"] = pyotp.random_base32()

    totp_secret = request.session["setup_totp_secret"]
    totp = pyotp.TOTP(totp_secret)
    provisioning_uri = totp.provisioning_uri(
        name=user.email or user.username,
        issuer_name="StatementHub",
    )

    # Generate QR code
    qr_img = qrcode.make(provisioning_uri, box_size=6, border=2)
    buffer = io.BytesIO()
    qr_img.save(buffer, format="PNG")
    qr_base64 = base64.b64encode(buffer.getvalue()).decode()

    if request.method == "POST":
        form = TOTPVerifyForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data["totp_code"]
            if totp.verify(code, valid_window=1):
                user.totp_secret = totp_secret
                user.totp_confirmed = True
                user.save(update_fields=["totp_secret", "totp_confirmed"])
                del request.session["setup_totp_secret"]
                messages.success(request, "Two-factor authentication has been enabled for your account.")
                return redirect(settings.LOGIN_REDIRECT_URL)
            else:
                form.add_error("totp_code", "Invalid code. Please try again.")
    else:
        form = TOTPVerifyForm()

    return render(request, "accounts/setup_2fa.html", {
        "form": form,
        "qr_base64": qr_base64,
        "totp_secret": totp_secret,
    })


# ---------------------------------------------------------------------------
# Password Reset — Admin-triggered (single user and bulk)
# ---------------------------------------------------------------------------


def _send_password_reset_email(request, user):
    """Send a password reset email to a single user in a background thread.

    Uses Django's built-in PasswordResetTokenGenerator for secure, time-limited
    tokens. The email is rendered synchronously (needs request context for
    build_absolute_uri), then dispatched to a daemon thread.
    """
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.http import urlsafe_base64_encode
    from django.utils.encoding import force_bytes

    uid = urlsafe_base64_encode(force_bytes(user.pk))
    token = default_token_generator.make_token(user)
    reset_url = request.build_absolute_uri(
        f"/accounts/reset/{uid}/{token}/"
    )

    subject = "Password Reset \u2014 StatementHub"
    html_message = render_to_string("accounts/email_password_reset.html", {
        "user": user,
        "reset_url": reset_url,
    })
    plain_message = strip_tags(html_message)
    from_email = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@statementhub.com.au")
    user_pk = user.pk
    user_email = user.email

    def _send():
        try:
            send_mail(
                subject=subject,
                message=plain_message,
                from_email=from_email,
                recipient_list=[user_email],
                html_message=html_message,
                fail_silently=False,
            )
            logger.info("Password reset email sent to %s (user_id=%s)", user_email, user_pk)
        except Exception as e:
            logger.error("Failed to send password reset email to %s (user_id=%s): %s", user_email, user_pk, e)

    thread = threading.Thread(target=_send, daemon=True)
    thread.start()


@login_required
@require_POST
def send_password_reset(request, pk):
    """Send a password reset email to a single user. Admin only."""
    if not request.user.is_admin:
        messages.error(request, "You do not have permission.")
        return redirect("review:dashboard")

    user = get_object_or_404(User, pk=pk)
    if not user.email:
        messages.warning(request, f"Cannot send reset email \u2014 {user.get_full_name() or user.username} has no email address.")
        return redirect("accounts:user_list")

    if not user.is_active:
        messages.warning(request, f"Cannot send reset email \u2014 {user.get_full_name() or user.username} is inactive.")
        return redirect("accounts:user_list")

    _send_password_reset_email(request, user)
    messages.success(request, f"Password reset email sent to {user.email}.")
    return redirect("accounts:user_list")


@login_required
@require_POST
def send_all_password_resets(request):
    """Send password reset emails to all active users with email addresses. Admin only."""
    if not request.user.is_admin:
        messages.error(request, "You do not have permission.")
        return redirect("review:dashboard")

    users = User.objects.filter(is_active=True).exclude(email="").exclude(email__isnull=True)
    count = 0
    for user in users:
        _send_password_reset_email(request, user)
        count += 1

    messages.success(request, f"Password reset emails are being sent to {count} user{'s' if count != 1 else ''}.")
    return redirect("accounts:user_list")


def password_reset_confirm_view(request, uidb64, token):
    """Handle the password reset confirmation \u2014 user sets a new password."""
    from django.contrib.auth.tokens import default_token_generator
    from django.utils.http import urlsafe_base64_decode
    from django.utils.encoding import force_str

    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None

    validlink = user is not None and default_token_generator.check_token(user, token)

    if request.method == "POST" and validlink:
        new_password1 = request.POST.get("new_password1", "")
        new_password2 = request.POST.get("new_password2", "")

        errors = []
        if new_password1 != new_password2:
            errors.append("The two passwords do not match.")
        if not new_password1:
            errors.append("Password cannot be empty.")

        if not errors:
            try:
                validate_password(new_password1, user=user)
            except DjangoValidationError as e:
                errors.extend(e.messages)

        if errors:
            return render(request, "accounts/password_reset_confirm.html", {
                "validlink": True,
                "password_errors": errors,
            })
        else:
            user.set_password(new_password1)
            user.save()
            logger.info("Password reset completed for user %s (user_id=%s)", user.username, user.pk)
            return redirect("accounts:password_reset_complete")

    return render(request, "accounts/password_reset_confirm.html", {
        "validlink": validlink,
    })


def password_reset_complete_view(request):
    """Simple confirmation page after a successful password reset."""
    return render(request, "accounts/password_reset_complete.html")
