# Copyright 2024-2026 .chance (dotchance)
# Licensed under the Apache License, Version 2.0. See LICENSE file.
"""VS-API's Kubernetes API clients.

VS-API runs only as a pod in the cluster, so the in-cluster service-account
configuration is the only configuration it loads. A pod without it fails
here, at its first Kubernetes call.
"""

from __future__ import annotations

import kubernetes.client
import kubernetes.config


def core_v1() -> kubernetes.client.CoreV1Api:
    """A CoreV1 client on the in-cluster configuration."""
    kubernetes.config.load_incluster_config()
    return kubernetes.client.CoreV1Api()


def custom_objects() -> kubernetes.client.CustomObjectsApi:
    """A CustomObjects client on the in-cluster configuration."""
    kubernetes.config.load_incluster_config()
    return kubernetes.client.CustomObjectsApi()
