# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""A stand-in for ``vs_api.kube_watch.watch_snapshots`` over fake API objects."""

from __future__ import annotations


async def replayed_snapshots(list_fn, *, timeout_s, **kwargs):
    """Each LIST of the fake API is one observed state; a consumer that is never
    satisfied runs into the timeout, as against the API server."""
    from vs_api.kube_watch import _listing_parts, _object_name

    for _observation in range(30):
        items, _resource_version = _listing_parts(list_fn(**kwargs))
        yield {_object_name(item): item for item in items}
    raise TimeoutError(f"no satisfying state within {timeout_s:.0f} s")
