"""CRUD endpoints for tasks."""

from flask import Blueprint, current_app, jsonify, request

from models import VALID_STATUSES
from utils.helpers import paginate, parse_int

tasks_bp = Blueprint("tasks", __name__)


def _store():
    return current_app.config["STORE"]


@tasks_bp.post("")
def create_task():
    payload = request.get_json(silent=True) or {}
    title = payload.get("title", "")
    task = _store().add_task(
        title=title,
        description=payload.get("description", ""),
        owner_id=payload.get("owner_id"),
    )
    return jsonify(task.to_dict()), 201


@tasks_bp.get("")
def list_tasks():
    page = parse_int(request.args.get("page"), default=1)
    per_page = parse_int(request.args.get("per_page"), default=10)
    tasks = sorted(_store().tasks.values(), key=lambda t: t.id)
    items = paginate([t.to_dict() for t in tasks], page, per_page)
    return jsonify({"page": page, "per_page": per_page, "items": items})


@tasks_bp.get("/<int:task_id>")
def get_task(task_id: int):
    task = _store().tasks[task_id]
    return jsonify(task.to_dict())


@tasks_bp.patch("/<int:task_id>")
def update_task(task_id: int):
    task = _store().tasks.get(task_id)
    if task is None:
        return jsonify({"error": "task not found"}), 404
    payload = request.get_json(silent=True) or {}
    if "status" in payload:
        if payload["status"] not in VALID_STATUSES:
            return jsonify({"error": f"status must be one of {VALID_STATUSES}"}), 400
        task.status = payload["status"]
    if "title" in payload:
        task.title = payload["title"]
    return jsonify(task.to_dict())


@tasks_bp.delete("/<int:task_id>")
def delete_task(task_id: int):
    if _store().tasks.pop(task_id, None) is None:
        return jsonify({"error": "task not found"}), 404
    return "", 204
