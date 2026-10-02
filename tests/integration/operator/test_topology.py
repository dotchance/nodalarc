"""Links come and go as the sky moves, and NodalArc keeps showing the truth afterward.

Change of topology is the normal condition of a session. The test lets the sky move, then asks
the routers and sends the packets again.
"""

from __future__ import annotations

import pytest

from .harness.client import Operator
from .harness.network import Clock, let_the_sky_move
from .truths import assert_session_is_truthful

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1200)]


def test_after_ground_links_change_the_session_still_shows_the_truth(
    operator: Operator, clock: Clock
) -> None:
    changes, started, ended = let_the_sky_move(operator)
    print(f"sky moved from {started} to {ended}: {changes}")
    if not changes:
        pytest.skip(f"no ground link of this session changed between {started} and {ended}")
    assert_session_is_truthful(operator)
