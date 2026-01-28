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
