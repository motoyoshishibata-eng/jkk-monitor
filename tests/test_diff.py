from datetime import datetime

from src.diff import detect, update_state
from src.models import Listing, State


def test_detect_new_listing():
    state = State()
    current = [Listing(name="A", room="1", rent=80000)]
    result = detect(current, state)
    assert len(result.new) == 1
    assert len(result.unchanged) == 0
    assert len(result.disappeared) == 0


def test_detect_unchanged():
    listing = Listing(name="A", room="1", rent=80000)
    state = State(known_listings={listing.key: listing})
    result = detect([listing], state)
    assert len(result.new) == 0
    assert len(result.unchanged) == 1


def test_detect_disappeared():
    listing = Listing(name="A", room="1", rent=80000)
    state = State(known_listings={listing.key: listing})
    result = detect([], state)
    assert len(result.disappeared) == 1


def test_update_state_preserves_first_seen_at():
    old = Listing(
        name="A", room="1", rent=80000, first_seen_at=datetime(2026, 1, 1)
    )
    state = State(known_listings={old.key: old})
    new_view = Listing(
        name="A", room="1", rent=80000, first_seen_at=datetime(2026, 5, 10)
    )
    new_state = update_state(state, [new_view])
    assert new_state.known_listings[old.key].first_seen_at == datetime(2026, 1, 1)


def test_update_state_adds_new_listing():
    state = State()
    new_listing = Listing(name="B", room="2", rent=90000)
    new_state = update_state(state, [new_listing])
    assert new_listing.key in new_state.known_listings


def test_update_state_drops_disappeared():
    """消失物件は known_listings から落とす（state は現状の鏡）。"""
    old = Listing(name="A", room="1", rent=80000)
    state = State(known_listings={old.key: old})
    new_state = update_state(state, [])
    assert old.key not in new_state.known_listings
