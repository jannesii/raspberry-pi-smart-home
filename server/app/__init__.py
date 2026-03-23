import logging

import eventlet

eventlet.monkey_patch()

logger = logging.getLogger(__name__)


def _create_flask_app():
    from flask import Flask

    from .config import load_settings

    logger.debug("_create_flask_app called")
    app = Flask(__name__)
    settings = load_settings()
    app.config.update(settings)
    logger.debug("Flask app created with %d config keys", len(settings))
    return app


def _register_media_routes(app) -> None:
    from pathlib import Path

    from flask import send_from_directory

    logger.debug("_register_media_routes called")
    hls_root = Path("/srv/hls")

    @app.route("/live/<path:filename>")
    def live(filename):
        logger.debug("Serving HLS file filename=%s", filename)
        return send_from_directory(hls_root, filename)

    @app.route("/favicon.ico")
    def favicon():
        logger.debug("Serving favicon")
        try:
            static_path = Path(app.static_folder or "static")
            return send_from_directory(static_path, "favicon.ico")
        except Exception:
            return ("", 404)


def _configure_http_security(app) -> None:
    from .security import apply_api_cors, apply_security_headers, configure_rate_limiting

    logger.debug("_configure_http_security called")
    configure_rate_limiting(app)
    apply_api_cors(app)
    apply_security_headers(app)


def _init_csrf(app) -> None:
    from .extensions import csrf

    logger.debug("_init_csrf called")
    csrf.init_app(app)
    logger.info("CSRF protection enabled (1 h token lifetime)")


def _init_controller(app):
    from .core import Controller

    db_path = app.config.get("DB_PATH")
    logger.debug("_init_controller called db_path=%s", db_path)
    if not db_path:
        raise RuntimeError("DB_PATH is missing - add to environment.")
    ctrl = Controller(db_path)
    app.ctrl = ctrl  # type: ignore[attr-defined]
    logger.info("Controller init: %s", db_path)
    return ctrl


def _init_sqlalchemy_engine(app, ctrl) -> None:
    from .core.sqlalchemy_engine import get_engine, get_engine_for_url

    db_path = app.config.get("DB_PATH")
    database_url = app.config.get("DATABASE_URL")
    logger.debug(
        "_init_sqlalchemy_engine called db_path=%s database_url_set=%s",
        db_path,
        bool(database_url),
    )
    try:
        if database_url:
            sa_engine = get_engine_for_url(database_url)
        else:
            sa_engine = get_engine(db_path)
        app.sa_engine = sa_engine  # type: ignore[attr-defined]
        ctrl._sa_engine = sa_engine
        logger.info("SQLAlchemy engine ready")
    except Exception as exc:
        logger.warning("Failed to initialize SQLAlchemy engine: %s", exc)


def _install_db_log_handler(ctrl) -> None:
    from .logging_handlers import DBLogHandler

    logger.debug("_install_db_log_handler called")
    try:
        db_handler = DBLogHandler(ctrl)
        db_handler.setLevel(logging.ERROR)
        db_handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        logging.getLogger().addHandler(db_handler)
    except Exception as exc:
        logger.warning("Failed to install DBLogHandler: %s", exc)


def _apply_logging_overrides(ctrl) -> None:
    from .logging_control import apply_logging_control_config

    logger.debug("_apply_logging_overrides called")
    try:
        cfg = ctrl.get_logging_control_config()  # type: ignore[attr-defined]
        if cfg:
            apply_logging_control_config(cfg)
            logger.info("Applied persisted logging control config")
    except Exception as exc:
        logger.warning("Failed to apply logging control config: %s", exc)


def _seed_admin_user(app, ctrl) -> None:
    logger.debug("_seed_admin_user called username=%s", app.config.get("WEB_USERNAME"))
    try:
        ctrl.register_user(  # type: ignore
            app.config["WEB_USERNAME"],
            password=app.config["WEB_PASSWORD"],
            is_admin=True,
            is_root_admin=True,
        )
        logger.info(
            "Seeded admin user %s (is_admin=True, is_root_admin=True)",
            app.config["WEB_USERNAME"],
        )
    except ValueError:
        ctrl.set_user_as_admin(app.config["WEB_USERNAME"], True)  # type: ignore
        logger.info("Ensured %s has is_admin=True (existing)", app.config["WEB_USERNAME"])


def _configure_login(app) -> None:
    from .blueprints.auth.auth import AuthAnonymous, kick_if_expired, load_user
    from .extensions import login_manager

    logger.debug("_configure_login called")
    login_manager.login_view = "auth.login"  # type: ignore
    login_manager.init_app(app)
    app.before_request(kick_if_expired)
    login_manager.user_loader(load_user)
    login_manager.anonymous_user = AuthAnonymous
    logger.info("Login manager ready")


def _configure_socketio(app, ctrl):
    from .extensions import socketio

    logger.debug("_configure_socketio called")
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
    _register_shutdown_handlers(ctrl, socketio)
    app.socketio = socketio  # type: ignore[attr-defined]
    logger.info("Socket.IO ready (origins: %s)", allowed_ws_origins)
    return socketio


def _register_shutdown_handlers(ctrl, socketio) -> None:
    import signal

    logger.debug("_register_shutdown_handlers called")

    def _shutdown_tasks():
        """Run best-effort shutdown side effects outside hub mainloop."""
        try:
            ctrl.log_message("Server shutting down", "system")  # type: ignore
        except Exception as exc:
            logging.getLogger(__name__).warning("Shutdown log_message failed: %s", exc)
        try:
            socketio.emit("server_shutdown")
            socketio.stop()
        except Exception as exc:
            logging.getLogger(__name__).warning("Shutdown emit failed: %s", exc)

    def exit_signal(signum, frame):
        del frame
        try:
            logging.getLogger(__name__).info("Caught exit signal %s", signum)
            eventlet.spawn_n(_shutdown_tasks)
        except Exception:
            pass

    signal.signal(signal.SIGTERM, exit_signal)
    signal.signal(signal.SIGINT, exit_signal)


def _init_services(app) -> None:
    from .services import init_services

    logger.debug("_init_services called")
    init_services(app)


def _register_blueprints(app) -> None:
    from .blueprints import register_blueprints

    logger.debug("_register_blueprints called")
    register_blueprints(app)


def _register_assets(app) -> None:
    from .assets import register_assets

    logger.debug("_register_assets called")
    register_assets(app)


def create_app():
    logger.info("Starting application")
    logger.debug("Debug logging enabled")
    app = _create_flask_app()
    _register_media_routes(app)
    _configure_http_security(app)
    _init_csrf(app)
    ctrl = _init_controller(app)
    _init_sqlalchemy_engine(app, ctrl)
    _install_db_log_handler(ctrl)
    _apply_logging_overrides(ctrl)
    _seed_admin_user(app, ctrl)
    _configure_login(app)
    socketio = _configure_socketio(app, ctrl)
    _init_services(app)
    _register_blueprints(app)
    _register_assets(app)
    return app, socketio
