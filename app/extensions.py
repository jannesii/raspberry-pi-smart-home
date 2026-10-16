"""Application-wide extension instances (initialized in create_app).

This module hosts singletons for Flask extensions so that other modules can
import them without creating circular imports. `create_app()` is responsible
for calling `init_app(...)` with runtime configuration.
"""

import os

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_socketio import SocketIO
from flask_wtf.csrf import CSRFProtect

# Rate limiter — configure storage via env, default to local Redis
_rate_storage = os.getenv("RATE_LIMIT_STORAGE_URI", "redis://localhost:6379")
_rate_storage_opts = {"socket_connect_timeout": 30}

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri=_rate_storage,
    storage_options=_rate_storage_opts,
    strategy="moving-window",
)

csrf = CSRFProtect()
login_manager = LoginManager()

# Socket.IO — parameters provided in app.init via init_app
socketio = SocketIO(logger=True)
