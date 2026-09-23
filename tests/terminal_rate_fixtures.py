# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""Terminal rates for Scheduler tests whose subject is not link shaping."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

from nodalarc.models.resolved_session import InterfaceRates


class AnyInterfaceRates(Mapping[tuple[str, str], InterfaceRates]):
    """Every WAN interface's terminal sends and receives at one rate.

    Stands in for ``ResolvedSession.interface_terminal_rates()`` where a test
    builds its links by hand and asserts nothing about rates.
    """

    def __init__(self, transmit_mbps: float = 1000.0, receive_mbps: float = 1000.0) -> None:
        self._rates = InterfaceRates(transmit_mbps, receive_mbps)

    def __getitem__(self, key: tuple[str, str]) -> InterfaceRates:
        return self._rates

    def __iter__(self) -> Iterator[tuple[str, str]]:
        return iter(())

    def __len__(self) -> int:
        return 0


ANY_INTERFACE_RATES = AnyInterfaceRates()
