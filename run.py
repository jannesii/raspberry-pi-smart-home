# run.py
import logging
import os

import dotenv

from app import create_app
from app.environment import is_development

# Development receives its whole configuration from the launcher; reading this
# checkout's production env file could fill gaps with production targets.
if not is_development():
    dotenv.load_dotenv("jannenkoti.env")


logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S")
logging.getLogger("socketio.server").setLevel(logging.WARNING)
app, socketio = create_app()

if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=int(os.getenv("PORT", 5555)))
