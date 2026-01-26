# run.py
import logging
import os

import dotenv

from app import create_app

dotenv.load_dotenv("jannenkoti.env")


logging.basicConfig(level=logging.INFO, datefmt="%H:%M:%S")
logging.getLogger("socketio.server").setLevel(logging.WARNING)
app, socketio = create_app()

if __name__ == "__main__":
    socketio.run(app, host="127.0.0.1", port=int(os.getenv("PORT", 5555)))
