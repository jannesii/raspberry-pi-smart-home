import logging
from typing import TYPE_CHECKING

from flask import (
    current_app,
    flash,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_limiter.errors import RateLimitExceeded
from flask_login import (
    AnonymousUserMixin,
    UserMixin,
    current_user,
    login_required,
    login_user,
    logout_user,
)

from ...extensions import csrf, limiter
from ...utils import safe_local_redirect_target
from . import auth_bp

if TYPE_CHECKING:
    from ...core import Controller

logger = logging.getLogger(__name__)


class AuthUser(UserMixin):
    """Flask-Login user, with admin flag."""

    def __init__(self, username: str, is_admin: bool = False):
        self.id = username
        self.is_admin = is_admin


class AuthAnonymous(AnonymousUserMixin):
    """Anonymous user with is_admin=False."""

    @property
    def is_admin(self):
        return False


def kick_if_expired():
    """Check if the user is temporary and expired."""
    ctrl: Controller = current_app.ctrl
    user = ctrl.get_user_by_username(current_user.get_id(), include_pw=False)
    if user and user.is_expired:
        logout_user()
        flash("Istuntosi on vanhentunut.", "warning")
        return redirect(url_for("auth.login"))


def load_user(user_id: str):
    ctrl: Controller = current_app.ctrl  # type: ignore
    user = ctrl.get_user_by_username(user_id)
    if user:
        is_admin = getattr(user, "is_admin", False)
        logger.debug("Loaded user %s (is_admin=%s)", user_id, is_admin)
        return AuthUser(user_id, is_admin=is_admin)
    logger.warning("User %s not found", user_id)
    return None


@auth_bp.errorhandler(RateLimitExceeded)
def handle_rate_limit(e):
    logger.warning("Rate limit reached for %s on %s", request.remote_addr, request.endpoint)
    return make_response(render_template("429.html", retry_after=e.description), 429)


@auth_bp.route("/login", methods=["GET", "POST"])
@limiter.limit(
    "5/minute;20/hour",
    exempt_when=lambda: (
        current_user.is_admin
        or (request.remote_addr or "").startswith("192.168.10.")
        or request.remote_addr in ["192.168.10.50", "192.168.0.3"]
    ),
)
def login():
    logger.info("Accessed /login via %s", request.method)
    ctrl: Controller = current_app.ctrl  # type: ignore

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        remember = request.form.get("remember") == "on"
        logger.debug("Login attempt for %s (remember=%s)", username, remember)

        user_obj = ctrl.get_user_by_username(username)
        if user_obj and user_obj.is_temporary and user_obj.expires_at:
            from datetime import datetime

            now = datetime.now(ctrl.finland_tz)
            expires_at = datetime.fromisoformat(user_obj.expires_at)
            if expires_at < now:
                flash("Tämä väliaikainen käyttäjätili on vanhentunut.", "error")
                logger.warning("Expired temporary account login attempt for %s", username)
                ctrl.log_message(
                    log_type="auth", message=f"Expired temporary login attempt for {username}"
                )
                return render_template("login.html")

        if ctrl.authenticate_user(username, password):
            session.permanent = remember
            login_user(
                # load_user will restore is_admin from DB on reload
                AuthUser(username, is_admin=getattr(user_obj, "is_admin", False)),
                remember=remember,
            )
            logger.info("User %s authenticated, is admin: %s", username, user_obj.is_admin)

            requested_next = request.args.get("next")
            next_page = safe_local_redirect_target(
                requested_next,
                url_for("web.get_home_page"),
            )
            if requested_next and next_page != requested_next:
                logger.warning("Rejected external login redirect target: %r", requested_next)
            response = redirect(next_page)
            logger.debug(
                "Rate-limit headers — remaining: %s, reset: %s",
                response.headers.get("X-RateLimit-Remaining"),
                response.headers.get("X-RateLimit-Reset"),
            )
            return response
        # Authentication failed
        flash("Virheellinen käyttäjätunnus tai salasana", "error")
        logger.warning("Auth failed for %s", username)
        ctrl.log_message(
            log_type="auth",
            message=f"Failed login attempt for {username}",
        )

    return render_template("login.html")


@auth_bp.route("/login_api", methods=["POST"])
@csrf.exempt
@limiter.limit(
    "10/minute;60/hour",
    exempt_when=lambda: (
        current_user.is_admin
        or (request.remote_addr or "").startswith("192.168.10.")
        or request.remote_addr in ["192.168.10.50", "192.168.0.3"]
    ),
)
def login_api():
    """
    Simple programmatic login with username/password.

    - Accepts JSON or form: { username, password, remember? }
    - CSRF-exempt to avoid token scraping from code.
    - On success sets the session cookie and returns JSON.
    """
    logger.info("Accessed /login_api via %s", request.method)
    ctrl: Controller = current_app.ctrl  # type: ignore

    # Parse credentials from JSON or form
    data = request.get_json(silent=True) or {}
    username = (
        (data.get("username") if isinstance(data, dict) else None)
        or request.form.get("username")
        or ""
    )
    password = (
        (data.get("password") if isinstance(data, dict) else None)
        or request.form.get("password")
        or ""
    )
    remember_raw = data.get("remember") if isinstance(data, dict) else None
    if remember_raw is None:
        remember_raw = request.form.get("remember")
    remember = bool(remember_raw) and str(remember_raw).lower() not in {"0", "false", "no"}

    username = username.strip()
    logger.debug("API login attempt for %s (remember=%s)", username, remember)

    # Check temporary expiry before auth
    user_obj = ctrl.get_user_by_username(username)
    if user_obj and user_obj.is_temporary and user_obj.expires_at:
        from datetime import datetime

        now = datetime.now(ctrl.finland_tz)
        try:
            expires_at = datetime.fromisoformat(user_obj.expires_at)
        except Exception:
            expires_at = None
        if expires_at and expires_at < now:
            ctrl.log_message(
                log_type="auth", message=f"Expired temporary login attempt for {username}"
            )
            return jsonify(
                {"ok": False, "error": "expired", "message": "Temporary user account expired."}
            ), 401

    if ctrl.authenticate_user(username, password):
        session.permanent = remember
        login_user(
            AuthUser(username, is_admin=getattr(user_obj, "is_admin", False)), remember=remember
        )
        logger.info(
            "User %s authenticated via /login_api (admin=%s)",
            username,
            getattr(user_obj, "is_admin", False),
        )
        return jsonify(
            {
                "ok": True,
                "user": username,
                "is_admin": bool(getattr(user_obj, "is_admin", False)),
            }
        )

    logger.warning("API auth failed for %s", username)
    ctrl.log_message(log_type="auth", message=f"Failed API login attempt for {username}")
    return jsonify(
        {"ok": False, "error": "invalid_credentials", "message": "Invalid username or password"}
    ), 401


@auth_bp.route("/logout")
@login_required
def logout():
    username = current_user.get_id()
    logout_user()
    session.pop("_flashes", None)
    logger.info("User %s logged out", username)
    return redirect(url_for("auth.login"))
