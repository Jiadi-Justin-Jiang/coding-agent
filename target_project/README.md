# Task Manager API (target codebase)

A deliberately small Flask API used as the playground for the coding agent.

    pip install flask pytest
    python -m pytest -q

Endpoints: `POST/GET /tasks`, `GET/PATCH/DELETE /tasks/<id>`, `POST /users`, `GET /users/<id>/tasks`.

> Contains intentional defects for the agent to find (see the agent repo's `docs/writeup.md`).
