"""Operational settings pages: logs and API keys."""
from __future__ import annotations

import logging

from flask import render_template, request, jsonify
from flask_login import login_required, current_user

from ...utils import (
    get_ctrl,
    require_admin_or_redirect,
    flash_error, flash_success,
)

from . import web_bp


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


@web_bp.route('/settings/logs')
@login_required
def logs():
    guard = require_admin_or_redirect(
        "Sinulla ei ole oikeuksia tarkastella lokitietoja.", 'web.get_settings_page')
    if guard:
        return guard
    ctrl = get_ctrl()
    # Get initial logs and available types
    initial_logs, has_more = ctrl.get_logs_filtered(limit=50)
    log_types = ctrl.get_log_types()
    total_count = ctrl.get_logs_count()
    return render_template(
        'logs.html',
        logs=initial_logs,
        has_more=has_more,
        log_types=log_types,
        total_count=total_count,
    )


@web_bp.route('/api/logs')
@login_required
def api_logs():
    """JSON API for filtered log retrieval with pagination."""
    if not getattr(current_user, 'is_admin', False):
        return jsonify({'error': 'Admin required'}), 403

    ctrl = get_ctrl()

    # Parse query parameters
    log_type = request.args.get('type', '').strip() or None
    search = request.args.get('search', '').strip() or None
    before_id = request.args.get('before_id', type=int)
    limit = min(request.args.get('limit', 50, type=int), 100)

    logs_list, has_more = ctrl.get_logs_filtered(
        log_type=log_type,
        search=search,
        before_id=before_id,
        limit=limit,
    )

    return jsonify({
        'logs': logs_list,
        'has_more': has_more,
    })


@web_bp.route('/settings/api_keys', methods=['GET', 'POST'])
@login_required
def api_keys():
    """Manage API keys: create and delete (secure storage)."""
    guard = require_admin_or_redirect(
        "Sinulla ei ole oikeuksia hallita API-avaimia.", 'web.get_settings_page')
    if guard:
        return guard

    ctrl = get_ctrl()
    created_token: str | None = None

    if request.method == 'POST':
        if 'create_key' in request.form:
            name = (request.form.get('key_name') or '').strip()
            if not name:
                flash_error('Anna nimi API-avaimelle.')
            else:
                # Prevent duplicate names (case-insensitive)
                existing = ctrl.list_api_keys()
                if any(k.name.lower() == name.lower() for k in existing):
                    flash_error('Samanniminen API-avain on jo olemassa.')
                else:
                    try:
                        _, token = ctrl.create_api_key(
                            name=name, created_by=current_user.get_id())
                        created_token = token  # show once
                        flash_success('API-avain luotu.')
                    except Exception as e:
                        flash_error(f'API-avaimen luonti epäonnistui: {e}')
        elif 'delete_key' in request.form:
            key_id = (request.form.get('delete_key') or '').strip()
            if not key_id:
                flash_error('Virheellinen avaimen tunniste.')
            else:
                try:
                    ctrl.delete_api_key(key_id)
                    flash_success('API-avain poistettu.')
                except Exception as e:
                    flash_error(f'Poisto epäonnistui: {e}')
        # Fall through to render

    keys = ctrl.list_api_keys()
    return render_template('api_keys.html', keys=keys, created_key=created_token)
