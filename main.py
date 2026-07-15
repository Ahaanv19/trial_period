# TextBack backend — Flask entry point.
#
# Serves:
#   * The TextBack message-analyzer API  (POST /api/analyze)  — see api/analyze.py
#   * A small themed "control center": landing page, login, and a user-management
#     console for admins.
#
# Run locally:  python main.py   →   http://localhost:8275
import logging
from urllib.parse import urljoin, urlparse

from flask import (abort, redirect, render_template, request, send_from_directory,
                   url_for, jsonify, current_app)
from flask_login import current_user, login_user, logout_user, login_required
from flask.cli import AppGroup

# Core app objects
from __init__ import app, db, login_manager

# Security helpers: RBAC, IP allowlist, audit logging, rate limiting.
# Imported defensively: if a security dependency is missing in some environment,
# fall back to no-op shims so the app still boots (degraded) instead of 502.
try:
    from utils.security import admin_required, ip_allowlist, audit, limiter
except Exception as _sec_err:  # noqa: BLE001
    logging.getLogger(__name__).error(
        "Security helpers unavailable (%s); running WITHOUT RBAC/rate-limit "
        "decorators. Install deps: pip install -r requirements.txt", _sec_err,
    )

    def admin_required(func):
        return func

    def ip_allowlist(*_args, **_kwargs):
        def decorator(func):
            return func
        return decorator

    def audit(*_args, **_kwargs):
        return None

    class _NoopLimiter:
        def limit(self, *_args, **_kwargs):
            def decorator(func):
                return func
            return decorator

    limiter = _NoopLimiter()

# API endpoints
from api.analyze import analyze_api  # TextBack message analyzer

# Database models
from model.user import User, initUsers
from model.subscription import Subscription, initSubscriptions
from model.analysis import Analysis, initAnalyses

# Register API blueprints
app.register_blueprint(analyze_api, url_prefix='/api')  # POST /api/analyze

# Tell Flask-Login the view function name of your login route
login_manager.login_view = "login"


@login_manager.unauthorized_handler
def unauthorized_callback():
    return redirect(url_for('login', next=request.path))


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


@app.context_processor
def inject_user():
    return dict(current_user=current_user)


# Helper function to check if the URL is safe for redirects
def is_safe_url(target):
    ref_url = urlparse(request.host_url)
    test_url = urlparse(urljoin(request.host_url, target))
    return test_url.scheme in ('http', 'https') and ref_url.netloc == test_url.netloc


@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("10 per minute", methods=["POST"])  # throttle brute-force attempts
def login():
    error = None
    next_page = request.args.get('next', '') or request.form.get('next', '')
    if request.method == 'POST':
        username = request.form.get('username', '')
        user = User.query.filter_by(_uid=username).first()
        if user and user.is_password(request.form.get('password', '')):
            login_user(user)
            audit("login_success", username=username)
            if not is_safe_url(next_page):
                return abort(400)
            return redirect(next_page or url_for('index'))
        else:
            audit("login_failure", username=username)
            error = 'Invalid username or password.'
    return render_template("login.html", error=error, next=next_page)


@app.route('/logout')
def logout():
    logout_user()
    return redirect(url_for('index'))


@app.errorhandler(404)  # catch for URL not found
def page_not_found(e):
    return render_template('404.html'), 404


@app.route('/')  # connects default URL to index() function
def index():
    return render_template("index.html")


@app.route('/users/table')
@login_required
@admin_required
@ip_allowlist()
def utable():
    users = User.query.all()
    # Build a plan/tier map so the UI can show each user's plan without extra
    # queries in the template. Admins are shown as 'admin'.
    tiers = {}
    for u in users:
        try:
            sub = Subscription.query.filter_by(_user_id=u.id).first()
        except Exception:
            sub = None
        if getattr(u, 'role', 'User') == 'Admin':
            tiers[u.id] = 'admin'
        elif sub and getattr(sub, 'status', None) == 'active':
            tiers[u.id] = sub.tier or 'free'
        else:
            tiers[u.id] = 'free'
    return render_template("utable.html", user_data=users, tiers=tiers)


@app.route('/users/table2')
@login_required
@admin_required
@ip_allowlist()
def u2table():
    # Consolidated into a single management console; keep old links working.
    return redirect(url_for('utable'))


# Helper function to extract uploads for a user
@app.route('/uploads/<path:filename>')
def uploaded_file(filename):
    return send_from_directory(current_app.config['UPLOAD_FOLDER'], filename)


@app.route('/users/delete/<int:user_id>', methods=['DELETE'])
@login_required
@admin_required
@ip_allowlist()
def delete_user(user_id):
    user = User.query.get(user_id)
    if user:
        user.delete()
        audit("user_deleted", target_user_id=user_id)
        return jsonify({'message': 'User deleted successfully'}), 200
    return jsonify({'error': 'User not found'}), 404


@app.route('/users/reset_password/<int:user_id>', methods=['POST'])
@login_required
@admin_required
@ip_allowlist()
def reset_password(user_id):
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    if user.update({"password": app.config['DEFAULT_PASSWORD']}):
        audit("password_reset", target_user_id=user_id)
        return jsonify({'message': 'Password reset successfully'}), 200
    audit("password_reset_failed", target_user_id=user_id)
    return jsonify({'error': 'Password reset failed'}), 500


@app.route('/users/set_role/<int:user_id>', methods=['POST'])
@login_required
@admin_required
@ip_allowlist()
def set_user_role(user_id):
    """Promote/demote a user between 'Admin' and 'User' (admin only)."""
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    data = request.get_json(silent=True) or {}
    role = (data.get('role') or '').strip()
    if role not in ('Admin', 'User'):
        return jsonify({'error': 'Invalid role. Must be "Admin" or "User".'}), 400
    # Guard: don't let an admin remove their own admin (avoid self lock-out).
    if user.id == current_user.id and role != 'Admin':
        return jsonify({'error': "You can't remove your own admin role."}), 400
    try:
        user.role = role
        db.session.commit()
        audit("role_changed", target_user_id=user_id, new_role=role)
        return jsonify({'message': f'Role updated to {role}', 'role': role}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/users/set_tier/<int:user_id>', methods=['POST'])
@login_required
@admin_required
@ip_allowlist()
def set_user_tier(user_id):
    """Set a user's subscription plan/tier (admin only)."""
    from datetime import datetime, timedelta
    user = User.query.get(user_id)
    if not user:
        return jsonify({'error': 'User not found'}), 404
    data = request.get_json(silent=True) or {}
    tier = (data.get('tier') or 'free').strip()
    billing_interval = data.get('billing_interval', 'monthly')
    if tier not in ('free', 'plus', 'pro'):
        return jsonify({'error': 'Invalid tier. Must be "free", "plus", or "pro".'}), 400
    days = 30 if billing_interval == 'monthly' else 365
    try:
        subscription = Subscription.query.filter_by(_user_id=user_id).first()
        if not subscription:
            subscription = Subscription(
                user_id=user_id,
                tier=tier,
                status='active',
                billing_interval=billing_interval if tier != 'free' else None,
            )
            db.session.add(subscription)
        else:
            subscription.tier = tier
            subscription.status = 'active'
            subscription.billing_interval = billing_interval if tier != 'free' else None
        subscription.expires_at = (datetime.utcnow() + timedelta(days=days)) if tier != 'free' else None
        db.session.commit()
        audit("tier_changed", target_user_id=user_id, new_tier=tier)
        return jsonify({'message': f'Plan updated to {tier}', 'tier': tier}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ---------------------------------------------------------------------------
# CLI: seed demo data
# ---------------------------------------------------------------------------
custom_cli = AppGroup('custom', help='Custom commands')


@custom_cli.command('generate_data')
def generate_data():
    for name, fn in (("initUsers", initUsers),
                     ("initSubscriptions", initSubscriptions),
                     ("initAnalyses", initAnalyses)):
        try:
            fn()
        except Exception as e:
            print(f"Error in {name}: {e}")


app.cli.add_command(custom_cli)


# ---------------------------------------------------------------------------
# Startup: make sure the tables we use exist (best-effort, never blocks boot).
# ---------------------------------------------------------------------------
with app.app_context():
    for model in (User, Subscription, Analysis):
        try:
            model.__table__.create(db.engine, checkfirst=True)
        except Exception as e:
            print(f"{getattr(model, '__tablename__', model)} table init: {e}")


# this runs the flask application on the development server
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=8275)
