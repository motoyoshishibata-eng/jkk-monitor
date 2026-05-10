"""state.json の読み書き。書き込みは tmp ファイル経由で原子的に行う。"""
from __future__ import annotations

from pathlib import Path

from .models import State

DEFAULT_STATE_PATH = Path("data/state.json")


def load_state(path: Path = DEFAULT_STATE_PATH) -> State:
    if not path.exists():
        return State()
    return State.model_validate_json(path.read_text(encoding="utf-8"))


def save_state(state: State, path: Path = DEFAULT_STATE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
    tmp.replace(path)
