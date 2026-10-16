"""Auth blueprint package.

Defines the `auth_bp` blueprint and imports the route module so that
all `@auth_bp.route` handlers are registered on import.
"""

from flask import Blueprint

auth_bp = Blueprint("auth", __name__)

# Import routes for side effects (register handlers on auth_bp)
from . import auth
