"""User endpoints."""

from flask import Blueprint, current_app, jsonify, request

from utils.helpers import is_valid_email

users_bp = Blueprint("users", __name__)


@users_bp.post("")
def create_user():
    payload = request.get_json(silent=True) or {}
    name = (payload.get("name") or "").strip()
    email = (payload.get("email") or "").strip()
    if not name:
        return jsonify({"error": "name is required"}), 400
    if not is_valid_email(email):
        return jsonify({"error": "invalid email"}), 400
    user = current_app.config["STORE"].add_user(name=name, email=email)
    return jsonify(user.to_dict()), 201


@users_bp.get("/<int:user_id>/tasks")
def user_tasks(user_id: int):
    store = current_app.config["STORE"]
    if user_id not in store.users:
        return jsonify({"error": "user not found"}), 404
    owned = [t.to_dict() for t in store.tasks.values() if t.owner_id == user_id]
    return jsonify(owned)
