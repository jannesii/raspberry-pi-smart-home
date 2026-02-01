import eventlet

eventlet.monkey_patch()


def create_app():
    import logging
    import signal
    from pathlib import Path

    logger = logging.getLogger(__name__)
    logger.info("Starting application")
    logger.debug("Debug logging enabled")

    # ─── Flask app & config ───
    from flask import Flask, request, send_from_directory

    app = Flask(__name__)
    from .config import load_settings

    settings = load_settings()
    app.config.update(settings)

    # ─── CORS for API endpoints ───
    @app.after_request
    def add_cors_headers(response):
        """
        Allow cross-origin requests (CORS) for /api/* endpoints.

        Uses wildcard origin (*) as requested.
        """
        try:
            path = request.path or ""
        except Exception:
            path = ""

        if path.startswith("/api/"):
            response.headers["Access-Control-Allow-Origin"] = "*"
            response.headers["Access-Control-Allow-Methods"] = (
                "GET, POST, PUT, PATCH, DELETE, OPTIONS"
            )
            response.headers["Access-Control-Allow-Headers"] = (
                "Content-Type, Authorization, X-Requested-With, X-API-Key"
            )
        return response

    HLS_ROOT = Path("/srv/hls")  # parent of “printer1”

    @app.route("/live/<path:filename>")
    def live(filename):
        return send_from_directory(HLS_ROOT, filename)

    # Serve /favicon.ico directly from the app static folder for browsers
    # that request it implicitly without the <link rel="icon"> tag.
    @app.route("/favicon.ico")
    def favicon():
        try:
            static_path = Path(app.static_folder or "static")
            return send_from_directory(static_path, "favicon.ico")
        except Exception:
            # If not found, send 404 via Flask default
            return ("", 404)

    # ─── Rate limiting ───
    from .security import apply_security_headers, configure_rate_limiting

    configure_rate_limiting(app)
    apply_security_headers(app)

    # ─── CSRF protection ───
    from .extensions import csrf

    csrf.init_app(app)
    logger.info("CSRF protection enabled (1 h token lifetime)")

    # ─── Domain-controller ───
    db_path = app.config.get("DB_PATH")
    logger.debug("Loaded DB_PATH config: %s", db_path)
    if not db_path:
        raise RuntimeError("DB_PATH is missing - add to environment.")
    from .core import Controller

    ctrl = Controller(db_path)
    app.ctrl = ctrl  # type: ignore
    logger.info("Controller init: %s", db_path)

    # ─── SQLAlchemy engine (phase-in for Alembic) ───
    try:
        from .core.sqlalchemy_engine import get_engine

        logger.debug("Initializing SQLAlchemy engine for DB_PATH=%s", db_path)
        app.sa_engine = get_engine(db_path)  # type: ignore[attr-defined]
        logger.info("SQLAlchemy engine ready")
    except Exception as e:
        logger.warning("Failed to initialize SQLAlchemy engine: %s", e)

    from time import sleep

    """ ctrl.migrate_3d_to_pg()
    logger.info("sleeping...")
    while True:
        sleep(1) """
    # ─── Route all ERROR+ logs into DB ───
    try:
        from .logging_handlers import DBLogHandler

        db_handler = DBLogHandler(ctrl)
        db_handler.setLevel(logging.ERROR)
        # Include exception tracebacks in the stored message
        db_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(db_handler)
    except Exception as e:
        logger.warning("Failed to install DBLogHandler: %s", e)

    # ─── Apply persisted logging overrides (if any) ───
    try:
        from .logging_control import apply_logging_control_config

        cfg = ctrl.get_logging_control_config()  # type: ignore[attr-defined]
        if cfg:
            apply_logging_control_config(cfg)
            logger.info("Applied persisted logging control config")
    except Exception as e:
        logger.warning("Failed to apply logging control config: %s", e)

    # ─── Seed admin user (Root-Admin) ───
    try:
        ctrl.register_user(  # type: ignore
            app.config["WEB_USERNAME"],
            password=app.config["WEB_PASSWORD"],
            is_admin=True,
            is_root_admin=True,
        )
        logger.info(
            "Seeded admin user %s (is_admin=True, is_root_admin=True)", app.config["WEB_USERNAME"]
        )
    except ValueError:
        ctrl.set_user_as_admin(app.config["WEB_USERNAME"], True)  # type: ignore
        logger.info("Ensured %s has is_admin=True (existing)", app.config["WEB_USERNAME"])

    # ─── Flask-Login ───
    from .extensions import login_manager

    login_manager.login_view = "auth.login"  # type: ignore
    login_manager.init_app(app)
    from .blueprints.auth.auth import AuthAnonymous, kick_if_expired, load_user

    app.before_request(kick_if_expired)
    login_manager.user_loader(load_user)
    login_manager.anonymous_user = AuthAnonymous
    logger.info("Login manager ready")

    # ─── Socket.IO ───
    from .extensions import socketio

    allowed_ws_origins = app.config.get("ALLOWED_WS_ORIGINS", [])
    logger.info("Allowed WebSocket origins: %s", allowed_ws_origins)

    socketio.init_app(
        app,
        async_mode="eventlet",
        message_queue="redis://localhost:6379",
        cors_allowed_origins=allowed_ws_origins,
        max_http_buffer_size=10 * 1024 * 1024,
        ping_interval=10,
        ping_timeout=20,
    )

    def _shutdown_tasks():
        """Run best-effort shutdown side effects outside hub mainloop.

        Avoid doing blocking work directly in the eventlet hub signal context.
        """
        try:
            ctrl.log_message("Server shutting down", "system")  # type: ignore
        except Exception as e:
            logging.getLogger(__name__).warning("Shutdown log_message failed: %s", e)
        try:
            socketio.emit("server_shutdown")
            socketio.stop()
        except Exception as e:
            logging.getLogger(__name__).warning("Shutdown emit failed: %s", e)

    def exit_signal(signum, frame):
        # Offload to a greenlet so we don't call blocking functions from the hub mainloop
        try:
            logging.getLogger(__name__).info("Caught exit signal %s", signum)
            eventlet.spawn_n(_shutdown_tasks)
        except Exception:
            # As a last resort, swallow errors to not interfere with Gunicorn shutdown
            pass
        # Let Gunicorn control worker shutdown; avoid calling socketio.stop() here

    signal.signal(signal.SIGTERM, exit_signal)
    signal.signal(signal.SIGINT, exit_signal)
    app.socketio = socketio  # type: ignore
    logger.info("Socket.IO ready (origins: %s)", allowed_ws_origins)

    # ─── Services (AC thermostat, Hue, Socket events) ───
    from .services import init_services

    init_services(app)

    # ─── Blueprints ───
    from .blueprints import register_blueprints

    register_blueprints(app)

    # ─── Template asset registry ───
    from .assets import register_assets

    register_assets(app)

    return app, socketio
