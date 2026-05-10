# Copyright 2024-2026 .chance (dotchance)
# Licensed under the NodalArc Source Available License 1.0. See LICENSE file.
"""Tests for shared TLE parsing helpers."""

from __future__ import annotations

import pytest
from nodalarc.tle import tle_age_days, tle_epoch_unix, tle_norad_id, validate_tle_pair

ISS_TLE_LINE_1 = "1 25544U 98067A   21075.51041667  .00001264  00000-0  29660-4 0  9993"
ISS_TLE_LINE_2 = "2 25544  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"


def test_tle_norad_id():
    assert tle_norad_id(ISS_TLE_LINE_1) == 25544


def test_validate_tle_pair_rejects_mismatched_catalog_ids():
    bad_line_2 = "2 99999  51.6442  21.5417 0002426  95.1670  21.8444 15.48974333273145"
    with pytest.raises(ValueError, match="line number mismatch"):
        validate_tle_pair(ISS_TLE_LINE_1, bad_line_2)


def test_tle_epoch_unix():
    assert tle_epoch_unix(ISS_TLE_LINE_1) == pytest.approx(1615896900.000288, abs=1e-6)


def test_tle_age_days_is_absolute():
    epoch = tle_epoch_unix(ISS_TLE_LINE_1)
    assert tle_age_days(ISS_TLE_LINE_1, epoch + 86400.0) == pytest.approx(1.0)
    assert tle_age_days(ISS_TLE_LINE_1, epoch - 86400.0) == pytest.approx(1.0)
