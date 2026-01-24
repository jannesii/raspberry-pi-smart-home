
import os
import tempfile
import pytz

class ControllerBase:
    def __init__(self, db_path: str = os.getenv("DB_PATH", os.path.join(tempfile.gettempdir(), "timelapse.db"))):
        from .. import DatabaseManager
        self.db = DatabaseManager(db_path)
        self.finland_tz = pytz.timezone('Europe/Helsinki')
