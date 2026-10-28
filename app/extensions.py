"""Application-wide extension instances (initialized in create_app).

This module hosts singletons for Flask extensions so that other modules can
import them without creating circular imports. `create_app()` is responsible
for calling `init_app(...)` with runtime configuration.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_login import LoginManager
from flask_socketio import SocketIO
from flask_wtf.csrf import CSRFProtect

# Rate limiter — storage comes from RATELIMIT_STORAGE_URI in the app config so it
# follows the selected environment's Redis (see app.config.load_settings).
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_options={"socket_connect_timeout": 30},
    strategy="moving-window",
)

csrf = CSRFProtect()
login_manager = LoginManager()

# Socket.IO — parameters provided in app.init via init_app
socketio = SocketIO(logger=True)
