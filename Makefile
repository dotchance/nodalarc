# NodalArc Build System
#
# make all       — from fresh checkout to running constellation
# make deps      — install all build-time dependencies
# make build     — build all container images
# make load      — import images into K3s containerd
# make install   — helm install the platform
# make deploy    — apply default ConstellationSpec CRD
# make test      — run unit tests
# make teardown  — full teardown via na-teardown.sh
# make clean     — remove build artifacts
#
# Machine-specific settings: copy config.mk.example to config.mk

-include config.mk

# ---------------------------------------------------------------------------
# Defaults (overridable via config.mk or environment)
# ---------------------------------------------------------------------------

KUBECONFIG      ?= /etc/rancher/k3s/k3s.yaml
K3S_NODE        ?= nodal
SUDO_CTR        ?= sudo
REGISTRY_PREFIX ?=
DEFAULT_SESSION ?= configs/sessions/starlink-176-isis-te.yaml
NAMESPACE       ?= nodalarc

export KUBECONFIG

# Image tag: git short SHA for reproducibility
GIT_SHA := $(shell git rev-parse --short HEAD 2>/dev/null || echo "dev")
TAG     ?= $(GIT_SHA)

# ---------------------------------------------------------------------------
# Image names
# ---------------------------------------------------------------------------

IMG_BASE       := $(REGISTRY_PREFIX)nodalarc/base:$(TAG)
IMG_FRR        := $(REGISTRY_PREFIX)nodalarc/frr:$(TAG)
IMG_PROBE      := $(REGISTRY_PREFIX)nodalarc/probe:$(TAG)
IMG_FWD        := $(REGISTRY_PREFIX)nodalarc/nodalpath-fwd:$(TAG)
IMG_OME        := $(REGISTRY_PREFIX)nodalarc/ome:$(TAG)
IMG_SCHEDULER  := $(REGISTRY_PREFIX)nodalarc/scheduler:$(TAG)
IMG_NODE_AGENT := $(REGISTRY_PREFIX)nodalarc/node-agent:$(TAG)
IMG_VS_API     := $(REGISTRY_PREFIX)nodalarc/vs-api:$(TAG)
IMG_OPERATOR   := $(REGISTRY_PREFIX)nodalarc/operator:$(TAG)
IMG_VF         := $(REGISTRY_PREFIX)nodalarc/vf:$(TAG)
IMG_NODALPATH  := $(REGISTRY_PREFIX)nodalarc/nodalpath:$(TAG)

BASE_IMAGES := $(IMG_BASE) $(IMG_FRR) $(IMG_PROBE) $(IMG_FWD)
SVC_IMAGES  := $(IMG_OME) $(IMG_SCHEDULER) $(IMG_NODE_AGENT) \
               $(IMG_VS_API) $(IMG_OPERATOR) $(IMG_VF) $(IMG_NODALPATH)
ALL_IMAGES  := $(BASE_IMAGES) $(SVC_IMAGES)

# ---------------------------------------------------------------------------
# Phony targets
# ---------------------------------------------------------------------------

.PHONY: all deps build load load-all install deploy test teardown clean \
        build-frontends build-images ensure-base-images build-base-images \
        build-base build-frr build-probe build-fwd \
        build-ome build-scheduler build-node-agent build-vs-api \
        build-operator build-vf build-nodalpath check-deps

# ---------------------------------------------------------------------------
# Composite targets
# ---------------------------------------------------------------------------

all: deps build load install deploy
	@echo ""
	@echo "=== NodalArc is running ==="
	@echo "VF:     http://localhost:3000"
	@echo "VS-API: http://localhost:8080"
	@echo ""

# ---------------------------------------------------------------------------
# deps — install all build-time dependencies (idempotent)
# ---------------------------------------------------------------------------

deps: check-deps
	@echo "[deps] Installing Python dependencies..."
	uv sync
	uv pip install -e lib/
	@echo "[deps] Installing VF frontend dependencies..."
	cd frontend && npm ci
	@echo "[deps] Installing NodalPath console dependencies..."
	cd nodalpath/console/frontend && npm ci
	@echo "[deps] Done."

check-deps:
	@command -v uv >/dev/null 2>&1    || { echo "ERROR: uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	@command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found."; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo "ERROR: kubectl not found."; exit 1; }
	@command -v helm >/dev/null 2>&1   || { echo "ERROR: helm not found."; exit 1; }
	@command -v node >/dev/null 2>&1   || { echo "ERROR: node not found. Install Node.js 22+."; exit 1; }

# ---------------------------------------------------------------------------
# build — compile frontends + build all Docker images
# ---------------------------------------------------------------------------

build: build-frontends build-images
	@echo "[build] All images built with tag $(TAG)."

build-frontends:
	@if [ ! -d frontend/dist ]; then \
		echo "[build] Building VF frontend..."; \
		cd frontend && npm run build; \
	else \
		echo "[build] VF frontend/dist exists — skipping (make clean to force rebuild)"; \
	fi
	@if [ ! -d nodalpath/console/frontend/dist ]; then \
		echo "[build] Building NodalPath console frontend..."; \
		cd nodalpath/console/frontend && npm run build; \
	else \
		echo "[build] NodalPath console dist exists — skipping"; \
	fi

# build-images builds service images (our code — always rebuild).
# Base images are checked and built only if missing from Docker cache.
build-images: ensure-base-images build-ome build-scheduler build-node-agent \
              build-vs-api build-operator build-vf build-nodalpath

ensure-base-images:
	@for img in nodalarc/base:latest nodalarc/frr:10 nodalarc/probe:latest nodalarc/nodalpath-fwd:latest; do \
		if ! docker image inspect $$img >/dev/null 2>&1; then \
			echo "[build] Base image $$img not found — building base images..."; \
			$(MAKE) build-base-images; \
			break; \
		fi; \
	done

build-base-images: build-base build-frr build-probe build-fwd

# Base images (base must build before frr)
build-base:
	docker build -t $(IMG_BASE) -t $(REGISTRY_PREFIX)nodalarc/base:latest images/base/

build-frr: build-base
	docker build -t $(IMG_FRR) -t $(REGISTRY_PREFIX)nodalarc/frr:latest images/frr/

build-probe:
	docker build -t $(IMG_PROBE) -t $(REGISTRY_PREFIX)nodalarc/probe:latest images/probe/

build-fwd:
	docker build -t $(IMG_FWD) -t $(REGISTRY_PREFIX)nodalarc/nodalpath-fwd:latest images/nodalpath-fwd/

# Service images (repo root context)
build-ome:
	docker build -f services/ome/Dockerfile -t $(IMG_OME) -t $(REGISTRY_PREFIX)nodalarc/ome:latest .

build-scheduler:
	docker build -f services/scheduler/Dockerfile -t $(IMG_SCHEDULER) -t $(REGISTRY_PREFIX)nodalarc/scheduler:latest .

build-node-agent:
	docker build -f services/node_agent/Dockerfile -t $(IMG_NODE_AGENT) -t $(REGISTRY_PREFIX)nodalarc/node-agent:latest .

build-vs-api:
	docker build -f services/vs_api/Dockerfile -t $(IMG_VS_API) -t $(REGISTRY_PREFIX)nodalarc/vs-api:latest .

build-operator:
	docker build -f services/nodalarc_operator/Dockerfile -t $(IMG_OPERATOR) -t $(REGISTRY_PREFIX)nodalarc/operator:latest .

build-nodalpath:
	docker build -f nodalpath/Dockerfile -t $(IMG_NODALPATH) -t $(REGISTRY_PREFIX)nodalarc/nodalpath:latest .

# Frontend image (frontend/ context, requires dist/)
build-vf: build-frontends
	docker build -t $(IMG_VF) -t $(REGISTRY_PREFIX)nodalarc/vf:latest frontend/

# ---------------------------------------------------------------------------
# load — import images into K3s containerd
# ---------------------------------------------------------------------------

SVC_IMAGES_LATEST := $(subst :$(TAG),:latest,$(SVC_IMAGES))

# Images that must be in k3s for Helm to work (values.yaml references)
REQUIRED_K3S_IMAGES := nodalarc/frr:10

load: load-base load-services

load-base:
	@echo "[load] Ensuring base images in K3s..."
	@for img in $(REQUIRED_K3S_IMAGES); do \
		if ! $(SUDO_CTR) k3s ctr images check "name==docker.io/$$img" 2>/dev/null | grep -q $$img; then \
			echo "  Loading $$img"; \
			docker save $$img | $(SUDO_CTR) k3s ctr images import - 2>&1 | tail -1; \
		else \
			echo "  $$img already in K3s"; \
		fi; \
	done

load-services:
	@echo "[load] Importing service images into K3s..."
	@for img in $(SVC_IMAGES) $(SVC_IMAGES_LATEST); do \
		echo "  $$img"; \
		docker save $$img | $(SUDO_CTR) k3s ctr images import - 2>&1 | tail -1; \
	done
	@echo "[load] Done."

# ---------------------------------------------------------------------------
# install — helm install the platform chart
# ---------------------------------------------------------------------------

install:
	@if kubectl get namespace $(NAMESPACE) >/dev/null 2>&1; then \
		echo "[install] Namespace $(NAMESPACE) exists — upgrading..."; \
		helm upgrade nodalarc deploy/helm --namespace $(NAMESPACE); \
	else \
		echo "[install] Installing Helm chart..."; \
		helm install nodalarc deploy/helm --namespace $(NAMESPACE) --create-namespace; \
	fi
	@echo "[install] Waiting for platform pods..."
	@kubectl wait --for=condition=Ready pod -l app=nodalarc-nats \
		-n $(NAMESPACE) --timeout=60s 2>/dev/null || true
	@kubectl wait --for=condition=Ready pod -l app=nodalarc-operator \
		-n $(NAMESPACE) --timeout=60s 2>/dev/null || true
	@sleep 5
	@echo "[install] Platform ready."

# ---------------------------------------------------------------------------
# deploy — apply a ConstellationSpec CRD
# ---------------------------------------------------------------------------

deploy:
	@echo "[deploy] Applying session: $(DEFAULT_SESSION)"
	@bash -c '\
		SESSION_YAML=$$(cat $(DEFAULT_SESSION)); \
		printf "apiVersion: nodalarc.io/v1alpha1\nkind: ConstellationSpec\nmetadata:\n  name: current-session\n  namespace: $(NAMESPACE)\nspec:\n  sessionYaml: |\n" > /tmp/_nodalarc_crd.yaml; \
		echo "$$SESSION_YAML" | sed "s/^/    /" >> /tmp/_nodalarc_crd.yaml; \
		kubectl apply -f /tmp/_nodalarc_crd.yaml; \
		rm -f /tmp/_nodalarc_crd.yaml'
	@echo "[deploy] Waiting for Ready (timeout 300s)..."
	@bash -c '\
		ELAPSED=0; \
		while [ $$ELAPSED -lt 300 ]; do \
			PHASE=$$(kubectl get constellationspec current-session \
				-n $(NAMESPACE) -o jsonpath="{.status.phase}" 2>/dev/null || echo "Unknown"); \
			if [ "$$PHASE" = "Ready" ]; then \
				PODS=$$(kubectl get pods -n $(NAMESPACE) --no-headers 2>/dev/null | wc -l); \
				echo "[deploy] Session ready. $$PODS pods running."; \
				exit 0; \
			fi; \
			if [ "$$PHASE" = "Error" ]; then \
				MSG=$$(kubectl get constellationspec current-session \
					-n $(NAMESPACE) -o jsonpath="{.status.message}" 2>/dev/null); \
				echo "[deploy] ERROR: $$MSG"; \
				exit 1; \
			fi; \
			if [ $$((ELAPSED % 15)) -eq 0 ] && [ $$ELAPSED -gt 0 ]; then \
				PODS=$$(kubectl get pods -n $(NAMESPACE) --no-headers 2>/dev/null | grep -c Running || true); \
				echo "  Phase: $$PHASE, Running: $$PODS ($${ELAPSED}s)"; \
			fi; \
			sleep 5; \
			ELAPSED=$$((ELAPSED + 5)); \
		done; \
		echo "[deploy] ERROR: Timed out after 300s"; exit 1'

# ---------------------------------------------------------------------------
# test — run unit tests
# ---------------------------------------------------------------------------

test:
	uv run pytest --ignore=tests/integration --tb=short -q

test-integration:
	uv run pytest tests/integration --tb=short -q

# ---------------------------------------------------------------------------
# teardown — full cluster teardown
# ---------------------------------------------------------------------------

teardown:
	bash tools/na-teardown.sh

# ---------------------------------------------------------------------------
# clean — remove build artifacts
# ---------------------------------------------------------------------------

clean:
	rm -rf frontend/dist nodalpath/console/frontend/dist
	find . -type d -name __pycache__ ! -path ./.venv/\* -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
	@echo "[clean] Build artifacts removed."
