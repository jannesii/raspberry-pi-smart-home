import logging
import os
import tempfile

import pytz

logger = logging.getLogger(__name__)


class ControllerBase:
    def __init__(
        self,
        db_path: str = os.getenv("DB_PATH", os.path.join(tempfile.gettempdir(), "timelapse.db")),
    ):
        logger.debug("ControllerBase.__init__ called db_path=%s", db_path)
        self.db_path = db_path
        from .. import DatabaseManager

        self.db = DatabaseManager(db_path)
        self.finland_tz = pytz.timezone("Europe/Helsinki")
        self._use_sa = os.getenv("USE_SQLA_READS", "").strip().lower() in (
            "1",
            "true",
            "yes",
            "on",
        )
        self._sa_engine = None
        if self._use_sa:
            try:
                from ..sqlalchemy_engine import get_engine, get_engine_for_url

                db_url = os.getenv("DATABASE_URL")
                if db_url:
                    logger.debug("ControllerBase using SQLAlchemy engine from DATABASE_URL")
                    self._sa_engine = get_engine_for_url(db_url)
                else:
                    logger.debug("ControllerBase using SQLAlchemy engine from DB_PATH")
                    self._sa_engine = get_engine(db_path)
            except Exception:
                logger.debug("ControllerBase failed to init SQLAlchemy engine", exc_info=True)
                self._sa_engine = None
                self._use_sa = False

    def database_available(self) -> bool | None:
        """Whether the application database answers a trivial query.

        ``None`` when no SQLAlchemy engine is configured. Used by readiness checks.
        """
        if self._sa_engine is None:
            return None
        from sqlalchemy import text

        try:
            with self._sa_engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            logger.warning("Database availability check failed: %s", type(exc).__name__)
            return False
        return True
