"""Entry point for the Task Manager API."""

from flask import Flask

from models import TaskStore
from routes.tasks import tasks_bp
from routes.users import users_bp


def create_app(store: TaskStore | None = None) -> Flask:
    """Build the Flask application with an (optionally injected) store."""
    app = Flask(__name__)
    app.config["STORE"] = store or TaskStore()
    app.register_blueprint(tasks_bp, url_prefix="/tasks")
    app.register_blueprint(users_bp, url_prefix="/users")
    return app


if __name__ == "__main__":
    create_app().run(debug=True)
