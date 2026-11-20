"""Importing this package registers every built-in trajectory."""

from coding_agent.trajectories import chat, correct, edit, explore, remember, run  # noqa: F401
from coding_agent.trajectories.base import REGISTRY  # noqa: F401
