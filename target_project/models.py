"""Data models and an in-memory store."""

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from itertools import count

VALID_STATUSES = ("todo", "in_progress", "done")


@dataclass
class User:
    id: int
    name: str
    email: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Task:
    id: int
    title: str
    description: str = ""
    status: str = "todo"
    owner_id: int | None = None
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> dict:
        return asdict(self)


class TaskStore:
    """Very small in-memory persistence layer."""

    def __init__(self) -> None:
        self.tasks: dict[int, Task] = {}
        self.users: dict[int, User] = {}
        self._task_ids = count(1)
        self._user_ids = count(1)

    def add_task(self, title: str, description: str = "", owner_id: int | None = None) -> Task:
        task = Task(id=next(self._task_ids), title=title,
                    description=description, owner_id=owner_id)
        self.tasks[task.id] = task
        return task

    def add_user(self, name: str, email: str) -> User:
        user = User(id=next(self._user_ids), name=name, email=email)
        self.users[user.id] = user
        return user
