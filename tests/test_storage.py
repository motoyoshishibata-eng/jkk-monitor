from src.models import Listing, State
from src.storage import load_state, save_state


def test_save_and_load_roundtrip(tmp_path):
    listing = Listing(name="A", room="1", rent=80000, address="小金井市本町")
    state = State(known_listings={listing.key: listing})

    path = tmp_path / "state.json"
    save_state(state, path)

    loaded = load_state(path)
    assert listing.key in loaded.known_listings
    assert loaded.known_listings[listing.key].name == "A"
    assert loaded.known_listings[listing.key].address == "小金井市本町"


def test_load_state_returns_empty_when_missing(tmp_path):
    path = tmp_path / "nonexistent.json"
    state = load_state(path)
    assert state.known_listings == {}
    assert state.last_checked_at is None


def test_save_creates_parent_directory(tmp_path):
    path = tmp_path / "subdir" / "state.json"
    save_state(State(), path)
    assert path.exists()
