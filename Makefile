# NodalArc Build System
# Run `make help` for available targets.

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
IMG_MI         := $(REGISTRY_PREFIX)nodalarc/measurement:$(TAG)

BASE_IMAGES := $(IMG_BASE) $(IMG_FRR) $(IMG_PROBE) $(IMG_FWD)
SVC_IMAGES  := $(IMG_OME) $(IMG_SCHEDULER) $(IMG_NODE_AGENT) \
               $(IMG_VS_API) $(IMG_OPERATOR) $(IMG_VF) $(IMG_NODALPATH)
# MI is not part of the default deployment — build/load explicitly with make build-measurement
OPT_IMAGES  := $(IMG_MI)
ALL_IMAGES  := $(BASE_IMAGES) $(SVC_IMAGES) $(OPT_IMAGES)
SVC_IMAGES_LATEST := $(subst :$(TAG),:latest,$(SVC_IMAGES))
REQUIRED_K3S_IMAGES := nodalarc/frr:10

# ---------------------------------------------------------------------------
# Phony targets
# ---------------------------------------------------------------------------

.PHONY: help all deps build load install deploy test test-integration \
        teardown clean status \
        build-frontends build-images ensure-base-images build-base-images \
        build-base build-frr build-probe build-fwd \
        build-ome build-scheduler build-node-agent build-vs-api \
        build-operator build-vf build-nodalpath build-measurement \
        load-base load-services check-deps \
        redeploy-ome redeploy-scheduler redeploy-node-agent redeploy-measurement \
        redeploy-vs-api redeploy-operator redeploy-vf redeploy-nodalpath

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------

help: ## Show this help
	@echo "NodalArc Build System"
	@echo ""
	@echo "Quick start:  sudo scripts/bootstrap-host.sh && sudo make all"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  sudo make all                          Full pipeline: deps → build → load → install → deploy"
	@echo "  sudo make build load                   Rebuild images and reimport into K3s"
	@echo "  sudo make deploy DEFAULT_SESSION=configs/sessions/starlink-176-nodalpath.yaml"
	@echo "  sudo make teardown                     Full teardown"
	@echo "  make test                              Run unit tests (no sudo needed)"
	@echo "  make -n build                          Dry-run — show what would be built"
	@echo ""
	@echo "Settings:  copy config.mk.example to config.mk"
	@echo "  KUBECONFIG      = $(KUBECONFIG)"
	@echo "  DEFAULT_SESSION = $(DEFAULT_SESSION)"
	@echo "  TAG             = $(TAG)"

# ---------------------------------------------------------------------------
# Composite targets
# ---------------------------------------------------------------------------

all: deps build load install deploy ## Full pipeline: checkout → running constellation
	@echo ""
	@echo "=== NodalArc is running ==="
	@echo "VF:     http://localhost:3000"
	@echo "VS-API: http://localhost:8080"
	@echo ""

# ---------------------------------------------------------------------------
# deps
# ---------------------------------------------------------------------------

deps: check-deps ## Install Python + Node.js dependencies (idempotent)
	@echo "[deps] Installing Python dependencies..."
	uv sync
	uv pip install -e lib/
	@echo "[deps] Installing VF frontend dependencies..."
	cd frontend && npm ci
	@echo "[deps] Installing NodalPath console dependencies..."
	cd nodalpath/console/frontend && npm ci
	@echo "[deps] Done."

check-deps:
	@command -v uv >/dev/null 2>&1    || { echo "ERROR: uv not found. Run: curl -LsSf https://astral.sh/uv/install.sh | sh"; exit 1; }
	@command -v docker >/dev/null 2>&1 || { echo "ERROR: docker not found. Run: sudo scripts/bootstrap-host.sh"; exit 1; }
	@command -v kubectl >/dev/null 2>&1 || { echo "ERROR: kubectl not found. Run: sudo scripts/bootstrap-host.sh"; exit 1; }
	@command -v helm >/dev/null 2>&1   || { echo "ERROR: helm not found. Run: sudo scripts/bootstrap-host.sh"; exit 1; }
	@command -v node >/dev/null 2>&1   || { echo "ERROR: node not found. Run: sudo scripts/bootstrap-host.sh"; exit 1; }

# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

build: build-frontends build-images ## Build frontend dist + all Docker images
	@echo "[build] All images built with tag $(TAG)."

build-frontends: ## Build VF and NodalPath console frontends
	@if [ ! -d frontend/dist ]; then \
		echo "[build] Building VF frontend..."; \
		cd frontend && npm run build; \
	else \
		echo "[build] VF frontend/dist exists — skipping (make clean to force)"; \
	fi
	@if [ ! -d nodalpath/console/frontend/dist ]; then \
		echo "[build] Building NodalPath console frontend..."; \
		cd nodalpath/console/frontend && npm run build; \
	else \
		echo "[build] NodalPath console dist exists — skipping"; \
	fi

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

build-base-images: build-base build-frr build-probe build-fwd ## Build infrastructure images (base, FRR, probe)

build-base:
	docker build -t $(IMG_BASE) -t $(REGISTRY_PREFIX)nodalarc/base:latest images/base/

build-frr: ## Build FRR image (official FRR base + our entrypoint)
	docker build -t $(IMG_FRR) -t $(REGISTRY_PREFIX)nodalarc/frr:latest -t $(REGISTRY_PREFIX)nodalarc/frr:10 images/frr/

build-probe:
	docker build -t $(IMG_PROBE) -t $(REGISTRY_PREFIX)nodalarc/probe:latest -f images/probe/Dockerfile .

build-fwd:
	docker build -t $(IMG_FWD) -t $(REGISTRY_PREFIX)nodalarc/nodalpath-fwd:latest images/nodalpath-fwd/

build-ome: ## Build OME image
	docker build -f services/ome/Dockerfile -t $(IMG_OME) -t $(REGISTRY_PREFIX)nodalarc/ome:latest .

build-scheduler: ## Build Scheduler image
	docker build -f services/scheduler/Dockerfile -t $(IMG_SCHEDULER) -t $(REGISTRY_PREFIX)nodalarc/scheduler:latest .

build-node-agent: ## Build Node Agent image
	docker build -f services/node_agent/Dockerfile -t $(IMG_NODE_AGENT) -t $(REGISTRY_PREFIX)nodalarc/node-agent:latest .

build-vs-api: ## Build VS-API image
	docker build -f services/vs_api/Dockerfile -t $(IMG_VS_API) -t $(REGISTRY_PREFIX)nodalarc/vs-api:latest .

build-operator: ## Build Operator image
	docker build -f services/nodalarc_operator/Dockerfile -t $(IMG_OPERATOR) -t $(REGISTRY_PREFIX)nodalarc/operator:latest .

build-nodalpath: ## Build NodalPath image
	docker build -f nodalpath/Dockerfile -t $(IMG_NODALPATH) -t $(REGISTRY_PREFIX)nodalarc/nodalpath:latest .

build-measurement: ## Build MI (Measurement) image
	docker build -f services/measurement/Dockerfile -t $(IMG_MI) -t $(REGISTRY_PREFIX)nodalarc/measurement:latest .

build-vf: build-frontends ## Build VF (visualization) image
	docker build -t $(IMG_VF) -t $(REGISTRY_PREFIX)nodalarc/vf:latest frontend/

# ---------------------------------------------------------------------------
# load
# ---------------------------------------------------------------------------

load: load-base load-services ## Import images into K3s containerd

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
# install
# ---------------------------------------------------------------------------

install: ## Helm install/upgrade the platform chart
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
# deploy
# ---------------------------------------------------------------------------

deploy: ## Apply ConstellationSpec CRD (DEFAULT_SESSION=path)
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
				RUNNING=$$(kubectl get pods -n $(NAMESPACE) --no-headers 2>/dev/null | grep -c Running || true); \
				echo "[deploy] Session ready. $$RUNNING/$$PODS pods running."; \
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
# redeploy-* — build, load, restart a single service
# ---------------------------------------------------------------------------

define load-and-restart
	@echo "[redeploy] Loading $1 into K3s..."
	@docker save $1 | $(SUDO_CTR) k3s ctr images import - 2>&1 | tail -1
	@docker save $(subst :$(TAG),:latest,$1) | $(SUDO_CTR) k3s ctr images import - 2>&1 | tail -1
	@echo "[redeploy] Restarting $2..."
	@kubectl rollout restart $2 -n $(NAMESPACE)
	@kubectl rollout status $2 -n $(NAMESPACE) --timeout=60s
endef

redeploy-ome: build-ome ## Build + load + restart OME
	$(call load-and-restart,$(IMG_OME),deployment/ome)

redeploy-scheduler: build-scheduler ## Build + load + restart Scheduler
	$(call load-and-restart,$(IMG_SCHEDULER),deployment/nodalarc-scheduler)

redeploy-node-agent: build-node-agent ## Build + load + restart Node Agent
	$(call load-and-restart,$(IMG_NODE_AGENT),daemonset/nodalarc-node-agent)

redeploy-vs-api: build-vs-api ## Build + load + restart VS-API
	$(call load-and-restart,$(IMG_VS_API),deployment/nodalarc-vs-api)

redeploy-operator: build-operator ## Build + load + restart Operator
	$(call load-and-restart,$(IMG_OPERATOR),deployment/nodalarc-operator)

redeploy-vf: build-vf ## Build + load + restart VF
	$(call load-and-restart,$(IMG_VF),deployment/nodalarc-vf)

redeploy-nodalpath: build-nodalpath ## Build + load + restart NodalPath
	$(call load-and-restart,$(IMG_NODALPATH),deployment/nodalpath)

redeploy-measurement: build-measurement ## Build + load + restart MI
	$(call load-and-restart,$(IMG_MI),deployment/nodalarc-measurement)

# ---------------------------------------------------------------------------
# status / test / teardown / clean
# ---------------------------------------------------------------------------

status: ## Show cluster status (pods, phase, links)
	@echo "=== Cluster Status ==="
	@kubectl get constellationspec current-session -n $(NAMESPACE) \
		-o jsonpath='Phase: {.status.phase}  Pods: {.status.readyPods}/{.status.totalPods}' 2>/dev/null || echo "No session deployed"
	@echo ""
	@kubectl get pods -n $(NAMESPACE) --no-headers 2>/dev/null | \
		awk '{status[$$3]++} END {for (s in status) printf "  %s: %d\n", s, status[s]}' || true

test: ## Run unit tests (868+, no sudo needed)
	uv run pytest --ignore=tests/integration --tb=short -q

test-integration: ## Run integration tests (requires running cluster)
	uv run pytest tests/integration --tb=short -q

teardown: ## Full teardown — pods, namespace, cluster resources, kernel state
	bash tools/na-teardown.sh

clean: ## Remove build artifacts (dist/, __pycache__, caches)
	rm -rf frontend/dist nodalpath/console/frontend/dist
	find . -type d -name __pycache__ ! -path ./.venv/\* -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
	@echo "[clean] Build artifacts removed."
