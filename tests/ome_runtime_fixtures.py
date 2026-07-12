"""Test doubles for the resolved OME runtime contract."""


class StaticOmeAddressing:
    """Explicit runtime node registry for isolated OME algorithm tests."""

    def __init__(
        self,
        *,
        satellite_ids: tuple[str, ...] = (),
        ground_station_ids: tuple[str, ...] = (),
        ground_aliases: dict[str, str] | None = None,
    ) -> None:
        self._node_types = {
            **dict.fromkeys(satellite_ids, "satellite"),
            **dict.fromkeys(ground_station_ids, "ground_station"),
        }
        self._ground_aliases = dict(ground_aliases or {})

    @property
    def has_type_registry(self) -> bool:
        return bool(self._node_types)

    def node_type(self, node_id: str) -> str:
        try:
            return self._node_types[node_id]
        except KeyError as exc:
            raise KeyError(f"unknown test runtime node_id {node_id!r}") from exc

    def is_ground_segment(self, node_id: str) -> bool:
        return self.node_type(node_id) == "ground_station"

    def is_satellite(self, node_id: str) -> bool:
        return self.node_type(node_id) == "satellite"

    def sat_id(self, plane: int, slot: int) -> str:
        raise KeyError(
            f"test runtime has no plane/slot identity for ({plane}, {slot}); use node_id"
        )

    def gs_id(self, name: str) -> str:
        try:
            return self._ground_aliases[name]
        except KeyError as exc:
            if name in self._node_types and self._node_types[name] == "ground_station":
                return name
            raise KeyError(f"unknown test ground station alias {name!r}") from exc
