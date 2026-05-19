# NodalArc Build System
# Run `make help` for available targets.
#
# The Makefile is a facade over lifecycle scripts. Keep Make responsible for
# the public command surface and simple dependency wiring; put stateful cluster
# orchestration in scripts/ where it can be tested directly.

-include config.mk

# ---------------------------------------------------------------------------
# Defaults (overridable via config.mk or environment)
# ---------------------------------------------------------------------------

KUBECONFIG      ?= /etc/rancher/k3s/k3s.yaml
K3S_NODE        ?= nodal
SUDO_CTR        ?= sudo
MODE            ?= auto
REGISTRY_HOST   ?= $(shell bash scripts/detect-registry.sh 2>/dev/null)
REGISTRY_PREFIX ?= $(if $(filter single-node,$(MODE)),,$(if $(REGISTRY_HOST),$(REGISTRY_HOST)/,))
DEFAULT_SESSION ?= configs/sessions/demo-36-ospf.yaml
NAMESPACE       ?= nodalarc
HELM_EXTRA_ARGS ?=
TEST_ROOT_PYTHON ?= .venv/bin/python

export KUBECONFIG

ifneq ($(strip $(REGISTRY_PREFIX)),)
ifeq ($(strip $(REGISTRY_HOST)),)
$(error REGISTRY_PREFIX is set without REGISTRY_HOST; set REGISTRY_HOST instead)
endif
endif

# Image tag: git short SHA for reproducibility
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo "dev")
TAG     ?= $(GIT_SHA)
BUILD_DATE ?= $(shell date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo unknown)
DOCKER_BUILD_METADATA_ARGS = --build-arg VCS_REF=$(GIT_SHA) --build-arg BUILD_DATE=$(BUILD_DATE)

# ---------------------------------------------------------------------------
# Image names
# ---------------------------------------------------------------------------

IMAGE_REF = $$(MODE='$(MODE)' REGISTRY_HOST='$(REGISTRY_HOST)' TAG='$(TAG)' NA_IMAGES_NO_CLUSTER=1 bash scripts/na-images.sh image-for $(1))
IMAGE_REF_TAG = $$(MODE='$(MODE)' REGISTRY_HOST='$(REGISTRY_HOST)' TAG='$(TAG)' NA_IMAGES_NO_CLUSTER=1 bash scripts/na-images.sh image-for-tag $(1) $(2))

# ---------------------------------------------------------------------------
# Phony targets
# ---------------------------------------------------------------------------

.PHONY: help all deps build load install reinstall upgrade session lint lint-policy dead-code \
        test test-integration test-root ensure-frontend-deps \
        teardown force-teardown reset-platform restart clean clean-deps clean-images \
        clean-registry purge-containerd nuke status check-registry test-backend test-frontend \
        build-frontends build-images ensure-base-images build-base-images \
        _clear-build-cache build-base build-frr build-probe build-fwd \
        build-ome build-scheduler build-node-agent build-vs-api \
        build-operator build-vf build-measurement \
        check-deps \
        deploy-all deploy-ome deploy-scheduler deploy-node-agent deploy-measurement \
        deploy-vs-api deploy-operator deploy-vf

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# Help and lifecycle map
# ---------------------------------------------------------------------------

help: ## Show this help
	@echo "NodalArc Build System"
	@echo "Copyright 2024-2026 .chance (dotchance)"
	@echo "Official source: https://github.com/dotchance/nodalarc"
	@echo ""
	@echo "Quick start from a clean checkout/K3s state:"
	@echo "  sudo scripts/bootstrap-host.sh"
	@echo "  make all"
	@echo ""
	@echo "Validated square-one lifecycle:"
	@echo "  make nuke && make all"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## ' Makefile | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "State transitions:"
	@echo "  Clean bring-up:       make all"
	@echo "  From scratch proof:   make nuke && make all"
	@echo "  Existing platform:    make build && make load && make upgrade"
	@echo "  Destructive refresh:  make build && make load && make reinstall && make session"
	@echo "  Session switch:       make session DEFAULT_SESSION=configs/sessions/starlink-176-isis.yaml"
	@echo "  Emergency only:       make force-teardown"
	@echo ""
	@echo "Notes:"
	@echo "  install refuses existing platform state; use upgrade or reinstall instead."
	@echo "  nuke removes NodalArc state, images, build artifacts, and dependencies; K3s remains."
	@echo "  force-teardown skips deterministic host cleanup and may leave kernel/container state behind."
	@echo "  sudo make test-root runs privileged Node Agent kernel proof tests on this host."
	@echo ""
	@echo "Settings:  copy config.mk.example to config.mk"
	@echo "  MODE            = $(MODE)"
	@echo "  REGISTRY_HOST   = $(REGISTRY_HOST)"
	@echo "  KUBECONFIG      = $(KUBECONFIG)"
	@echo "  DEFAULT_SESSION = $(DEFAULT_SESSION)"
	@echo "  TAG             = $(TAG)"

# ---------------------------------------------------------------------------
# Primary lifecycle entry point
# ---------------------------------------------------------------------------
#
# This is the normal clean-state path after bootstrap-host has prepared the
# machine. It intentionally includes load before install so Helm never starts
# pods whose images have not been placed where K3s can pull them.

all: deps build ## Clean-state pipeline: deps → build → load → install → session → status
	$(MAKE) load install session
	@echo ""
	@echo "=== NodalArc is running ==="
	@$(MAKE) status
	@echo ""
	@echo "[all] Next: use 'make status' to inspect, 'make session DEFAULT_SESSION=...' to switch sessions, or 'make build && make load && make upgrade' after source changes."
	@echo ""

# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------
#
# deps is idempotent setup for the local development machine. It installs
# Python and frontend dependencies, but it does not build images or touch K3s.

deps: check-deps ## Install Python + Node.js dependencies (idempotent)
	@echo "[deps] Installing Python dependencies..."
	uv sync --extra dev
	uv pip install -e lib/
	@echo "[deps] Installing VF frontend dependencies..."
	cd frontend && npm ci
	@echo "[deps] Checking for high-severity vulnerabilities..."
	@cd frontend && HIGH=$$(npm audit --audit-level=high 2>&1 | grep -c "high\|critical" || true); \
		if [ "$$HIGH" -gt 0 ]; then \
			echo ""; \
			echo "========================================================"; \
			echo "  SECURITY: High-severity npm vulnerabilities detected!"; \
			echo "  DO NOT deploy this build."; \
			echo "  Pull the latest source: git pull origin main"; \
			echo "  If the issue persists, report it."; \
			echo "========================================================"; \
			echo ""; \
			npm audit 2>&1; \
			exit 1; \
		fi
	@echo "[deps] Done."

check-deps:
	@command -v uv >/dev/null 2>&1    || { echo "ERROR: uv not found. Run: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	@command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found. Run: sudo scripts/bootstrap-host.sh"; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo "ERROR: kubectl not found. Run: sudo scripts/bootstrap-host.sh"; exit 1; }
	@command -v helm >/dev/null 2>&1   || { echo "ERROR: helm not found. Run: sudo scripts/bootstrap-host.sh"; exit 1; }
	@command -v node >/dev/null 2>&1   || { echo "ERROR: node not found. Run: sudo scripts/bootstrap-host.sh"; exit 1; }

# ---------------------------------------------------------------------------
# Registry diagnostics
# ---------------------------------------------------------------------------
#
# Multi-node clusters need an image registry that every node can pull from.
# This target explains what Make resolved and fails early if the registry is
# unreachable from the developer host.

check-registry: ## Report resolved REGISTRY_HOST and verify the registry is reachable
	@if [ -z "$(REGISTRY_HOST)" ]; then \
		echo "[check-registry] REGISTRY_HOST is empty — single-node mode."; \
		echo "[check-registry]   'make load' will import images directly into K3s containerd."; \
		echo "[check-registry]   Set REGISTRY_HOST (or populate /etc/rancher/k3s/registries.yaml"; \
		echo "[check-registry]   with a mirror entry) to enable multi-node mode."; \
	else \
		echo "[check-registry] REGISTRY_HOST   = $(REGISTRY_HOST)"; \
		echo "[check-registry] REGISTRY_PREFIX = $(REGISTRY_PREFIX)"; \
		printf "[check-registry] Probing http://%s/v2/_catalog ... " "$(REGISTRY_HOST)"; \
		if curl -sf --max-time 5 "http://$(REGISTRY_HOST)/v2/_catalog" >/dev/null 2>&1; then \
			echo "OK"; \
		else \
			echo "FAIL"; \
			echo "[check-registry] ERROR: registry at $(REGISTRY_HOST) is not reachable from this host."; \
			echo "[check-registry]   - Is the registry running?  (docker ps | grep registry)"; \
			echo "[check-registry]   - Is the hostname resolvable here?  (getent hosts $(REGISTRY_HOST))"; \
			echo "[check-registry]   - Does /etc/rancher/k3s/registries.yaml on every cluster node"; \
			echo "[check-registry]     declare this host as a mirror?  (that is the cluster operator's"; \
			echo "[check-registry]     job; we don't check remote nodes from here)"; \
			exit 1; \
		fi; \
	fi

# ---------------------------------------------------------------------------
# Build artifacts
# ---------------------------------------------------------------------------
#
# Build targets produce local artifacts only: frontend dist/ directories and
# Docker images tagged with the current git SHA. They do not install anything
# into Kubernetes.

build: deps build-frontends build-images ## Build frontend dist + all Docker images
	@echo "[build] All images built with tag $(TAG)."
	@echo "[build] Next: make load"

build-frontends: ## Build VF frontend
	@echo "[build] Building VF frontend..."
	cd frontend && npm run build

build-images: _clear-build-cache build-base-images build-ome build-scheduler build-node-agent \
              build-vs-api build-operator build-vf

_clear-build-cache:
	@docker builder prune --filter type=source.local -f >/dev/null 2>&1 || true

ensure-base-images:
	@for logical in base frr probe; do \
		img=$$(MODE='$(MODE)' REGISTRY_HOST='$(REGISTRY_HOST)' TAG='$(TAG)' NA_IMAGES_NO_CLUSTER=1 bash scripts/na-images.sh image-for-tag $$logical latest); \
		if ! docker image inspect $$img >/dev/null 2>&1; then \
			echo "[build] Base image $$img not found — building base images..."; \
			$(MAKE) build-base-images; \
			break; \
		fi; \
	done

build-base-images: build-base build-frr build-probe ## Build infrastructure images (base, FRR, probe)

build-base:
	docker build $(DOCKER_BUILD_METADATA_ARGS) -t "$(call IMAGE_REF,base)" -t "$(call IMAGE_REF_TAG,base,latest)" images/base/

build-frr: ## Build FRR image (official FRR base + our entrypoint)
	docker build $(DOCKER_BUILD_METADATA_ARGS) -t "$(call IMAGE_REF,frr)" -t "$(call IMAGE_REF_TAG,frr,latest)" -t "$(call IMAGE_REF_TAG,frr,10)" images/frr/

build-probe:
	docker build $(DOCKER_BUILD_METADATA_ARGS) -t "$(call IMAGE_REF,probe)" -t "$(call IMAGE_REF_TAG,probe,latest)" -f images/probe/Dockerfile .

build-ome: ## Build OME image
	docker build $(DOCKER_BUILD_METADATA_ARGS) -f services/ome/Dockerfile -t "$(call IMAGE_REF,ome)" -t "$(call IMAGE_REF_TAG,ome,latest)" .

build-scheduler: ## Build Scheduler image
	docker build $(DOCKER_BUILD_METADATA_ARGS) -f services/scheduler/Dockerfile -t "$(call IMAGE_REF,scheduler)" -t "$(call IMAGE_REF_TAG,scheduler,latest)" .

build-node-agent: ## Build Node Agent image
	docker build $(DOCKER_BUILD_METADATA_ARGS) -f services/node_agent/Dockerfile -t "$(call IMAGE_REF,node-agent)" -t "$(call IMAGE_REF_TAG,node-agent,latest)" .

build-vs-api: ## Build VS-API image
	docker build $(DOCKER_BUILD_METADATA_ARGS) -f services/vs_api/Dockerfile -t "$(call IMAGE_REF,vs-api)" -t "$(call IMAGE_REF_TAG,vs-api,latest)" .

build-operator: ## Build Operator image
	docker build $(DOCKER_BUILD_METADATA_ARGS) -f services/nodalarc_operator/Dockerfile -t "$(call IMAGE_REF,operator)" -t "$(call IMAGE_REF_TAG,operator,latest)" .

build-measurement: ## Build MI (Measurement) image
	docker build $(DOCKER_BUILD_METADATA_ARGS) -f services/measurement/Dockerfile -t "$(call IMAGE_REF,measurement)" -t "$(call IMAGE_REF_TAG,measurement,latest)" .

build-vf: build-frontends ## Build VF (visualization) image
	docker build $(DOCKER_BUILD_METADATA_ARGS) --build-arg BUILD_HASH=$(GIT_SHA) -t "$(call IMAGE_REF,vf)" -t "$(call IMAGE_REF_TAG,vf,latest)" frontend/

# ---------------------------------------------------------------------------
# Image transport
# ---------------------------------------------------------------------------
#
# load is the bridge between local Docker builds and the cluster runtime:
# single-node imports into K3s containerd; multi-node pushes to REGISTRY_HOST.

load: ## Import images into K3s (single-node) or push to registry (multi-node)
	@MODE='$(MODE)' REGISTRY_HOST='$(REGISTRY_HOST)' TAG='$(TAG)' SUDO_CTR='$(SUDO_CTR)' KUBECONFIG='$(KUBECONFIG)' NAMESPACE='$(NAMESPACE)' bash scripts/na-load-images.sh

# ---------------------------------------------------------------------------
# Platform lifecycle
# ---------------------------------------------------------------------------
#
# Platform targets own the Helm release and long-running platform pods. They
# do not create or switch emulation sessions; session is a separate lifecycle
# transition below.

# install waits for EVERY platform Deployment to reach Available AND the
# Node Agent DaemonSet to finish rolling out. Any failure (timeout,
# missing node label, readiness probe failing) surfaces as exit-nonzero
# — no silent "Platform ready" lie. The desired=0 check catches the
# common footgun where no nodes carry the nodalarc.io/node-agent=true
# label and the DaemonSet silently schedules zero pods.
install: ## Helm install the platform chart; refuses existing platform state
	@ACTION=install MODE='$(MODE)' REGISTRY_HOST='$(REGISTRY_HOST)' TAG='$(TAG)' SUDO_CTR='$(SUDO_CTR)' KUBECONFIG='$(KUBECONFIG)' NAMESPACE='$(NAMESPACE)' HELM_EXTRA_ARGS='$(HELM_EXTRA_ARGS)' bash scripts/na-install-platform.sh

reinstall: ## Explicit destructive reinstall through official teardown
	@ACTION=reinstall MODE='$(MODE)' REGISTRY_HOST='$(REGISTRY_HOST)' TAG='$(TAG)' SUDO_CTR='$(SUDO_CTR)' KUBECONFIG='$(KUBECONFIG)' NAMESPACE='$(NAMESPACE)' HELM_EXTRA_ARGS='$(HELM_EXTRA_ARGS)' bash scripts/na-install-platform.sh

# ---------------------------------------------------------------------------
# Session lifecycle and platform restarts
# ---------------------------------------------------------------------------
#
# session creates or switches the active emulation workload after the platform
# is healthy. restart is a blunt operational tool for already-installed
# platform pods; it does not change Helm values or session state.

session: ## Start a session (DEFAULT_SESSION=path/to/session.yaml)
	@KUBECONFIG='$(KUBECONFIG)' NAMESPACE='$(NAMESPACE)' DEFAULT_SESSION='$(DEFAULT_SESSION)' bash scripts/na-session.sh

restart: ## Rolling restart all platform pods (forces image re-pull)
	@echo "[restart] Rolling restart of all platform deployments and daemonsets..."
	@for dep in ome nodalarc-scheduler nodalarc-vs-api nodalarc-operator nodalarc-vf; do \
		kubectl get deployment/$$dep -n $(NAMESPACE) >/dev/null; \
		kubectl rollout restart deployment/$$dep -n $(NAMESPACE); \
		echo "  Restarted deployment/$$dep"; \
	done
	@kubectl get daemonset/nodalarc-node-agent -n $(NAMESPACE) >/dev/null
	@kubectl rollout restart daemonset/nodalarc-node-agent -n $(NAMESPACE)
	@echo "  Restarted daemonset/nodalarc-node-agent"
	@echo "[restart] Waiting for rollout..."
	@for dep in ome nodalarc-scheduler nodalarc-vs-api nodalarc-operator nodalarc-vf; do \
		kubectl rollout status deployment/$$dep -n $(NAMESPACE) --timeout=60s; \
	done
	@kubectl rollout status daemonset/nodalarc-node-agent -n $(NAMESPACE) --timeout=60s
	@echo "[restart] Done."

# ---------------------------------------------------------------------------
# upgrade — in-place Helm upgrade without teardown
# ---------------------------------------------------------------------------
#
# Updates image tags and Helm values via `helm upgrade` without
# tearing down the namespace. Use after committing to update the Helm
# release with the new git SHA tags. Session pods are not affected.
#
# Workflow:
#   git commit → make build && make load && make upgrade

upgrade: ## In-place Helm upgrade (updates image tags, no teardown)
	@ACTION=upgrade MODE='$(MODE)' REGISTRY_HOST='$(REGISTRY_HOST)' TAG='$(TAG)' SUDO_CTR='$(SUDO_CTR)' KUBECONFIG='$(KUBECONFIG)' NAMESPACE='$(NAMESPACE)' HELM_EXTRA_ARGS='$(HELM_EXTRA_ARGS)' bash scripts/na-install-platform.sh

# ---------------------------------------------------------------------------
# Iterative service deploys
# ---------------------------------------------------------------------------
#
# deploy-* is for fast inner-loop development against an already installed
# platform. It is not the first-install path and it is not a replacement for
# build/load/upgrade when you want Helm values to reflect a new committed SHA.
# Each target builds one image, transports that image, then restarts only the
# matching Deployment or DaemonSet.
#
# Iterative dev loop — no commit needed:
#   edit code → make deploy-vs-api → test in browser → repeat
#
# How it works:
#   1. Build the one service image (Docker layer cache makes unchanged
#      layers instant — only the COPY layer with your code change rebuilds)
#   2. Load/push the selected :SHA tag. The :SHA tag
#      replaces the previous image at that tag. The Deployment spec
#      already references :SHA from the last make install.
#   3. kubectl rollout restart forces a new pod. imagePullPolicy=Always
#      (set in config.mk for multi-node) ensures it pulls from the
#      registry instead of using the node's containerd cache.
#
# No Helm involved — the Deployment spec doesn't change (same :SHA tag),
# only the image content behind the tag changes in the registry. This
# avoids Helm drift while supporting rapid iteration without commits.
#
# After committing: make build && make load && make upgrade
# This updates Helm with the new SHA tags (post-commit permanent state).

define _deploy-service
	@MODE='$(MODE)' REGISTRY_HOST='$(REGISTRY_HOST)' TAG='$(TAG)' SUDO_CTR='$(SUDO_CTR)' KUBECONFIG='$(KUBECONFIG)' NAMESPACE='$(NAMESPACE)' bash scripts/na-deploy-service.sh $1 $2
endef

deploy-all: build-ome build-scheduler build-node-agent build-vs-api build-operator build-vf ## Build + load + restart all platform services
	$(call _deploy-service,ome,deployment/ome)
	$(call _deploy-service,scheduler,deployment/nodalarc-scheduler)
	$(call _deploy-service,node-agent,daemonset/nodalarc-node-agent)
	$(call _deploy-service,vs-api,deployment/nodalarc-vs-api)
	$(call _deploy-service,operator,deployment/nodalarc-operator)
	$(call _deploy-service,vf,deployment/nodalarc-vf)

deploy-ome: build-ome ## Build + load + restart OME
	$(call _deploy-service,ome,deployment/ome)

deploy-scheduler: build-scheduler ## Build + load + restart Scheduler
	$(call _deploy-service,scheduler,deployment/nodalarc-scheduler)

deploy-node-agent: build-node-agent ## Build + load + restart Node Agent
	$(call _deploy-service,node-agent,daemonset/nodalarc-node-agent)

deploy-vs-api: build-vs-api ## Build + load + restart VS-API
	$(call _deploy-service,vs-api,deployment/nodalarc-vs-api)

deploy-operator: build-operator ## Build + load + restart Operator
	$(call _deploy-service,operator,deployment/nodalarc-operator)

deploy-vf: build-vf ## Build + load + restart VF
	$(call _deploy-service,vf,deployment/nodalarc-vf)

deploy-measurement: build-measurement ## Build + load + restart MI
	$(call _deploy-service,measurement,deployment/nodalarc-measurement)

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

status: ## Show cluster status (pods, phase, links)
	@MODE='$(MODE)' KUBECONFIG='$(KUBECONFIG)' NAMESPACE='$(NAMESPACE)' REGISTRY_HOST='$(REGISTRY_HOST)' DEFAULT_SESSION='$(DEFAULT_SESSION)' TAG='$(TAG)' bash scripts/na-status.sh

# ---------------------------------------------------------------------------
# Lint and static analysis
# ---------------------------------------------------------------------------

lint: lint-policy ## Run lint, formatting, and high-confidence dead-code checks
	uv run --extra dev ruff check .
	uv run --extra dev ruff format --check .
	$(MAKE) dead-code

lint-policy: ## Verify lint policy was not weakened
	uv run --extra dev python scripts/check-lint-policy.py

dead-code: ## Report high-confidence unused code findings
	uv run --extra dev vulture lib services tools scripts images --min-confidence 80

# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
#
# Unit tests should not require a live cluster. Integration tests are explicit
# because they assume a running platform or cluster-adjacent services.

ensure-frontend-deps: frontend/node_modules/.bin/vitest

frontend/node_modules/.bin/vitest: frontend/package.json frontend/package-lock.json
	@echo "[deps] Installing VF frontend dependencies..."
	cd frontend && npm ci

test: ensure-frontend-deps ## Run all unit tests (no sudo needed)
	@backend=0; frontend=0; \
	uv run --extra dev pytest --ignore=tests/integration --tb=short -q || backend=$$?; \
	cd frontend && npm test || frontend=$$?; \
	if [ $$backend -ne 0 ] || [ $$frontend -ne 0 ]; then \
		echo ""; echo "[test] FAILURES: backend=$$backend frontend=$$frontend"; exit 1; \
	fi

test-backend: ## Run Python unit tests
	uv run --extra dev pytest --ignore=tests/integration --tb=short -q

test-frontend: ensure-frontend-deps ## Run frontend unit tests (vitest)
	cd frontend && npm test

test-integration: ## Run integration tests (requires running cluster)
	uv run --extra dev pytest tests/integration --tb=short -q

test-root: ## Run privileged Node Agent kernel proof tests (requires root/CAP_NET_ADMIN)
	@if [ "$$(id -u)" != "0" ]; then \
		echo "FATAL: test-root requires root/CAP_NET_ADMIN. Run: sudo make test-root"; \
		exit 1; \
	fi
	@if [ ! -x "$(TEST_ROOT_PYTHON)" ]; then \
		echo "FATAL: $(TEST_ROOT_PYTHON) not found or not executable. Run: uv sync --extra dev"; \
		exit 1; \
	fi
	@echo "[test-root] Running privileged Node Agent substrate proof tests"
	$(TEST_ROOT_PYTHON) -m pytest -m requires_root tests/integration/test_node_agent_netem.py --tb=short -q

# ---------------------------------------------------------------------------
# Reset and teardown
# ---------------------------------------------------------------------------
#
# teardown is the normal deterministic cleanup path. force-teardown is a
# break-glass Kubernetes removal when the lifecycle tooling itself is broken;
# it intentionally warns because it can leave host/container state behind.

teardown: ## Full teardown — pods, namespace, cluster resources, kernel state
	@KUBECONFIG='$(KUBECONFIG)' NAMESPACE='$(NAMESPACE)' bash scripts/na-teardown.sh

force-teardown: ## Break-glass Kubernetes removal only; does not verify host cleanup
	@echo "[force-teardown] BREAK-GLASS: deterministic cleanup will not be performed."
	@helm uninstall nodalarc -n $(NAMESPACE) --ignore-not-found --timeout=120s 2>/dev/null || true
	@kubectl delete namespace $(NAMESPACE) --timeout=30s 2>/dev/null || true
	@echo "[force-teardown] Next: make nuke to verify square-one cleanup before redeploying."

reset-platform: ## Teardown platform and runtime caches but keep dependencies
	@$(MAKE) clean-registry
	@$(MAKE) purge-containerd
	@$(MAKE) teardown
	@$(MAKE) clean
	@echo "[reset-platform] Next: make build && make load && make install && make session"

# ---------------------------------------------------------------------------
# Local cleanup
# ---------------------------------------------------------------------------
#
# These targets clean files and images. They are intentionally separate from
# teardown so a developer can remove build products without disturbing a
# running cluster.

clean: ## Remove build artifacts (dist/, caches)
	rm -rf frontend/dist
	find . -type d -name __pycache__ ! -path ./.venv/\* -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
	@echo "[clean] Build artifacts removed."

clean-deps: ## Remove installed dependencies (.venv, node_modules)
	rm -rf .venv lib/nodalarc.egg-info
	rm -rf frontend/node_modules
	@echo "[clean-deps] Dependencies removed."

clean-images: ## Remove all nodalarc Docker images
	@bash scripts/na-clean-images.sh

# clean-registry owns only external registry contents. K3s containerd cache
# cleanup belongs to purge-containerd so nuke can preserve required ordering.
clean-registry: ## Purge nodalarc images from REGISTRY_HOST
	@REGISTRY_HOST='$(REGISTRY_HOST)' bash scripts/clean-registry.sh

purge-containerd: ## Purge nodalarc images from K3s containerd caches
	@KUBECONFIG='$(KUBECONFIG)' NAMESPACE='$(NAMESPACE)' SUDO_CTR='$(SUDO_CTR)' bash scripts/na-purge-containerd.sh

# Ordering matters: remote containerd purge needs Node Agent pods alive, so
# nuke runs registry cleanup and remote cache purge before teardown.
nuke: ## Remove everything — registry + teardown + images + deps + artifacts
	@MODE='$(MODE)' REGISTRY_HOST='$(REGISTRY_HOST)' KUBECONFIG='$(KUBECONFIG)' NAMESPACE='$(NAMESPACE)' SUDO_CTR='$(SUDO_CTR)' bash scripts/na-nuke.sh
