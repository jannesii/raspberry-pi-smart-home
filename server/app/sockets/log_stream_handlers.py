"""
Log stream socket event handlers.

All functions receive the SocketEventHandler instance as the first argument.
"""
from __future__ import annotations

import logging
import re
import subprocess
import threading
from typing import TYPE_CHECKING, Any, Dict

from flask import request
from flask_login import current_user

if TYPE_CHECKING:
    from ._handlers import SocketEventHandler

logger = logging.getLogger(__name__)


def handle_log_stream(handler: "SocketEventHandler", data: Dict[str, Any]) -> None:
    """Handle log stream subscription/unsubscription."""
    if not current_user.is_authenticated:
        return
    if not getattr(current_user, 'is_admin', False):
        handler.socketio.emit(
            'error', {'message': 'Admin required for log stream'})
        return

    sid = request.sid
    action = str(data.get('action', '')).lower()

    try:
        if action == 'subscribe':
            lines = int(data.get('lines', 50))
            lines = min(max(lines, 10), 500)  # Clamp 10-500

            handler._log_stream_sids.add(sid)
            logger.info(
                "Log stream subscriber added: %s (lines=%d)", sid, lines)

            # Start the stream if not running
            start_log_stream(handler, lines)

        elif action == 'unsubscribe':
            handler._log_stream_sids.discard(sid)
            logger.info("Log stream subscriber removed: %s", sid)
            maybe_stop_log_stream(handler)

        else:
            handler.socketio.emit(
                'error', {'message': f'Invalid log_stream action: {action}'})

    except Exception as e:
        logger.exception("log_stream error: %s", e)
        handler.socketio.emit('error', {'message': 'Log stream error'})


def start_log_stream(handler: "SocketEventHandler", initial_lines: int = 50) -> None:
    """Start the journalctl subprocess if not running."""
    if handler._log_stream_process is not None:
        # Already running
        return

    def stream_reader():
        try:
            # Start journalctl with follow mode
            # -o short-iso: cleaner timestamp format
            handler._log_stream_process = subprocess.Popen(
                ['/usr/bin/journalctl', '-u', 'jannenkoti.service',
                 '-f', '-n', str(initial_lines), '--no-pager', '-o', 'short-iso'],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )

            for line in iter(handler._log_stream_process.stdout.readline, ''):
                if handler._log_stream_process is None:
                    break
                if not handler._log_stream_sids:
                    break

                line = line.rstrip('\n')
                if line:
                    # Strip "hostname process[pid]: " prefix
                    # Format: "2026-01-25T17:16:48+0200 raspberrypi gunicorn[123]: actual message"
                    # We want: "17:16:48 actual message"
                    match = re.match(
                        r'^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})\+\d{4}\s+\S+\s+\S+\[\d+\]:\s*(.*)$',
                        line
                    )
                    if match:
                        line = f"{match.group(1)} {match.group(2)}"

                    # Emit to all subscribers
                    for sub_sid in list(handler._log_stream_sids):
                        try:
                            handler.socketio.emit(
                                'log_line', {'line': line}, to=sub_sid)
                        except Exception:
                            handler._log_stream_sids.discard(sub_sid)

        except Exception as e:
            logger.error("Log stream reader error: %s", e)
        finally:
            cleanup_log_stream(handler)

    thread = threading.Thread(target=stream_reader, daemon=True)
    thread.start()


def maybe_stop_log_stream(handler: "SocketEventHandler") -> None:
    """Stop the log stream if no subscribers remain."""
    if not handler._log_stream_sids:
        cleanup_log_stream(handler)


def cleanup_log_stream(handler: "SocketEventHandler") -> None:
    """Clean up the journalctl subprocess."""
    if handler._log_stream_process is not None:
        try:
            handler._log_stream_process.terminate()
            handler._log_stream_process.wait(timeout=2)
        except Exception:
            try:
                handler._log_stream_process.kill()
            except Exception:
                pass
        handler._log_stream_process = None
        logger.info("Log stream process terminated")
