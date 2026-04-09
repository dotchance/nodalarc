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
DEFAULT_SESSION ?= configs/sessions/demo-36-ospf.yaml
NAMESPACE       ?= nodalarc
HELM_EXTRA_ARGS ?=

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
REQUIRED_K3S_IMAGES := nodalarc/frr:latest

# ---------------------------------------------------------------------------
# Phony targets
# ---------------------------------------------------------------------------

.PHONY: help all deps build load install session test test-integration \
        teardown clean clean-deps clean-images nuke status \
        build-frontends build-images ensure-base-images build-base-images \
        build-base build-frr build-probe build-fwd \
        build-ome build-scheduler build-node-agent build-vs-api \
        build-operator build-vf build-nodalpath build-measurement \
        check-deps \
        deploy-all deploy-ome deploy-scheduler deploy-node-agent deploy-measurement \
        deploy-vs-api deploy-operator deploy-vf deploy-nodalpath

.DEFAULT_GOAL := help

# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------

help: ## Show this help
	@echo "NodalArc Build System"
	@echo ""
	@echo "Quick start:  sudo scripts/bootstrap-host.sh && make all"
	@echo ""
	@echo "Targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## ' Makefile | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2}'
	@echo ""
	@echo "Examples:"
	@echo "  make all                               Full pipeline: deps → build → sudo load/install/session"
	@echo "  make build && sudo make load            Rebuild images and push to registry"
	@echo "  sudo make session DEFAULT_SESSION=configs/sessions/starlink-176-nodalpath.yaml"
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

all: deps build ## Full pipeline: checkout → running constellation
	sudo make load install session
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
	@if [ -d nodalpath/console/frontend ]; then \
		echo "[deps] Installing NodalPath console dependencies..."; \
		cd nodalpath/console/frontend && npm ci; \
	fi
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

build: deps build-frontends build-images ## Build frontend dist + all Docker images
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
	@for img in nodalarc/base:latest nodalarc/frr:latest nodalarc/probe:latest nodalarc/nodalpath-fwd:latest; do \
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

ifeq ($(REGISTRY_PREFIX),)
# Single-node: import directly into local K3s containerd
load: ## Import images into K3s (single-node) or push to registry (multi-node)
	@echo "[load] Importing images into K3s containerd..."
	@for img in $(REQUIRED_K3S_IMAGES); do \
		if ! $(SUDO_CTR) k3s ctr images check "name==docker.io/$$img" 2>/dev/null | grep -q $$img; then \
			echo "  $$img"; \
			docker save $$img | $(SUDO_CTR) k3s ctr images import - 2>&1 | tail -1; \
		else \
			echo "  $$img already in K3s"; \
		fi; \
	done
	@for img in $(SVC_IMAGES) $(SVC_IMAGES_LATEST); do \
		echo "  $$img"; \
		docker save $$img | $(SUDO_CTR) k3s ctr images import - 2>&1 | tail -1; \
	done
	@echo "[load] Done."
else
# Multi-node: push to container registry (images already tagged with REGISTRY_PREFIX by build)
load:
	@echo "[load] Pushing images to registry $(REGISTRY_PREFIX)..."
	@for img in $(REGISTRY_PREFIX)nodalarc/frr:latest $(REGISTRY_PREFIX)nodalarc/node-agent:latest \
		$(REGISTRY_PREFIX)nodalarc/ome:latest $(REGISTRY_PREFIX)nodalarc/scheduler:latest \
		$(REGISTRY_PREFIX)nodalarc/vs-api:latest $(REGISTRY_PREFIX)nodalarc/operator:latest \
		$(REGISTRY_PREFIX)nodalarc/vf:latest $(REGISTRY_PREFIX)nodalarc/nodalpath:latest; do \
		echo "  $$img"; \
		docker push $$img 2>&1 | tail -1; \
	done
	@echo "[load] Done."
endif

# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

install: ## Helm install/upgrade the platform chart
	@echo "[install] Installing Helm chart..."
	@if helm status nodalarc -n $(NAMESPACE) >/dev/null 2>&1; then \
		echo "[install] Removing existing installation..."; \
		helm uninstall nodalarc -n $(NAMESPACE) 2>/dev/null || true; \
		kubectl delete namespace $(NAMESPACE) --timeout=30s 2>/dev/null || true; \
		echo "[install] Waiting for namespace cleanup..."; \
		while kubectl get namespace $(NAMESPACE) 2>/dev/null | grep -q $(NAMESPACE); do sleep 2; done; \
	fi
	@NODAL_NODE=$$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo ""); \
		if [ -n "$$NODAL_NODE" ]; then \
			echo "[install] Auto-detected node: $$NODAL_NODE"; \
			helm install nodalarc deploy/helm --namespace $(NAMESPACE) --create-namespace \
				--set controlPlaneNode=$$NODAL_NODE --set sessionNodeName=$$NODAL_NODE $(HELM_EXTRA_ARGS); \
		else \
			helm install nodalarc deploy/helm --namespace $(NAMESPACE) --create-namespace $(HELM_EXTRA_ARGS); \
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

session: ## Start a session (DEFAULT_SESSION=path/to/session.yaml)
	@echo "[session] Starting: $(DEFAULT_SESSION)"
	@echo "[session] Waiting for platform..."
	@bash -c 'for i in $$(seq 1 30); do \
		kubectl get crd constellationspecs.nodalarc.io &>/dev/null && break; \
		sleep 2; \
	done'
	@bash -c '\
		SESSION_YAML=$$(cat $(DEFAULT_SESSION)); \
		printf "apiVersion: nodalarc.io/v1alpha1\nkind: ConstellationSpec\nmetadata:\n  name: current-session\n  namespace: $(NAMESPACE)\nspec:\n  sessionYaml: |\n" > /tmp/_nodalarc_crd.yaml; \
		echo "$$SESSION_YAML" | sed "s/^/    /" >> /tmp/_nodalarc_crd.yaml; \
		kubectl apply -f /tmp/_nodalarc_crd.yaml; \
		rm -f /tmp/_nodalarc_crd.yaml'
	@echo "[session] Waiting for Ready (timeout 300s)..."
	@bash -c '\
		ELAPSED=0; \
		while [ $$ELAPSED -lt 300 ]; do \
			PHASE=$$(kubectl get constellationspec current-session \
				-n $(NAMESPACE) -o jsonpath="{.status.phase}" 2>/dev/null || echo "Unknown"); \
			if [ "$$PHASE" = "Ready" ]; then \
				PODS=$$(kubectl get pods -n $(NAMESPACE) --no-headers 2>/dev/null | wc -l); \
				RUNNING=$$(kubectl get pods -n $(NAMESPACE) --no-headers 2>/dev/null | grep -c Running || true); \
				NOT_RUNNING=$$(kubectl get pods -n $(NAMESPACE) --no-headers 2>/dev/null | grep -v Running | grep -v Completed || true); \
				if [ -n "$$NOT_RUNNING" ]; then \
					echo "[session] WARNING: Phase is Ready but some pods are not running:"; \
					echo "$$NOT_RUNNING"; \
					echo "[session] $$RUNNING/$$PODS pods running. Check pod status."; \
					exit 1; \
				fi; \
				echo "[session] Session ready. $$RUNNING/$$PODS pods running."; \
				exit 0; \
			fi; \
			if [ "$$PHASE" = "Error" ]; then \
				MSG=$$(kubectl get constellationspec current-session \
					-n $(NAMESPACE) -o jsonpath="{.status.message}" 2>/dev/null); \
				echo "[session] ERROR: $$MSG"; \
				exit 1; \
			fi; \
			if [ $$((ELAPSED % 15)) -eq 0 ] && [ $$ELAPSED -gt 0 ]; then \
				PODS=$$(kubectl get pods -n $(NAMESPACE) --no-headers 2>/dev/null | grep -c Running || true); \
				echo "  Phase: $$PHASE, Running: $$PODS ($${ELAPSED}s)"; \
			fi; \
			sleep 5; \
			ELAPSED=$$((ELAPSED + 5)); \
		done; \
		echo "[session] ERROR: Timed out after 300s"; exit 1'

# ---------------------------------------------------------------------------
# deploy-* — build, load, restart a single service
# ---------------------------------------------------------------------------

ifeq ($(REGISTRY_PREFIX),)
define _load-image
	@docker save $1 | $(SUDO_CTR) k3s ctr images import - 2>&1 | tail -1
	@docker save $(subst :$(TAG),:latest,$1) | $(SUDO_CTR) k3s ctr images import - 2>&1 | tail -1
endef
else
define _load-image
	@docker push $(subst :$(TAG),:latest,$1) 2>&1 | tail -1
endef
endif

define _deploy-service
	@echo "[deploy] Loading $1..."
	$(call _load-image,$1)
	@echo "[deploy] Restarting $2..."
	@kubectl rollout restart $2 -n $(NAMESPACE)
	@kubectl rollout status $2 -n $(NAMESPACE) --timeout=60s
endef

deploy-all: build-ome build-scheduler build-node-agent build-vs-api build-operator build-vf ## Build + load + restart all core services
	$(call _deploy-service,$(IMG_OME),deployment/ome)
	$(call _deploy-service,$(IMG_SCHEDULER),deployment/nodalarc-scheduler)
	$(call _deploy-service,$(IMG_NODE_AGENT),daemonset/nodalarc-node-agent)
	$(call _deploy-service,$(IMG_VS_API),deployment/nodalarc-vs-api)
	$(call _deploy-service,$(IMG_OPERATOR),deployment/nodalarc-operator)
	$(call _deploy-service,$(IMG_VF),deployment/nodalarc-vf)

deploy-ome: build-ome ## Build + load + restart OME
	$(call _deploy-service,$(IMG_OME),deployment/ome)

deploy-scheduler: build-scheduler ## Build + load + restart Scheduler
	$(call _deploy-service,$(IMG_SCHEDULER),deployment/nodalarc-scheduler)

deploy-node-agent: build-node-agent ## Build + load + restart Node Agent
	$(call _deploy-service,$(IMG_NODE_AGENT),daemonset/nodalarc-node-agent)

deploy-vs-api: build-vs-api ## Build + load + restart VS-API
	$(call _deploy-service,$(IMG_VS_API),deployment/nodalarc-vs-api)

deploy-operator: build-operator ## Build + load + restart Operator
	$(call _deploy-service,$(IMG_OPERATOR),deployment/nodalarc-operator)

deploy-vf: build-vf ## Build + load + restart VF
	$(call _deploy-service,$(IMG_VF),deployment/nodalarc-vf)

deploy-nodalpath: build-nodalpath ## Build + load + restart NodalPath
	$(call _deploy-service,$(IMG_NODALPATH),deployment/nodalpath)

deploy-measurement: build-measurement ## Build + load + restart MI
	$(call _deploy-service,$(IMG_MI),deployment/nodalarc-measurement)

# ---------------------------------------------------------------------------
# status / test / teardown / clean
# ---------------------------------------------------------------------------

status: ## Show cluster status (pods, phase, links)
	@bash -c '\
		echo "=== NodalArc Status ==="; \
		echo ""; \
		\
		if ! kubectl cluster-info >/dev/null 2>&1; then \
			echo "Cluster: CANNOT REACH"; \
			echo "  Check that Kubernetes is running and KUBECONFIG is readable."; \
			echo "  For K3s: sudo chmod 644 /etc/rancher/k3s/k3s.yaml"; \
			exit 0; \
		fi; \
		\
		echo "Cluster:"; \
		NODE_COUNT=$$(kubectl get nodes --no-headers 2>/dev/null | wc -l); \
		NODE_READY=$$(kubectl get nodes --no-headers 2>/dev/null | grep -c " Ready" || echo 0); \
		if [ "$$NODE_COUNT" -eq 1 ]; then \
			echo "  Single node ($$NODE_READY/$$NODE_COUNT ready)"; \
		else \
			echo "  Multi-node ($$NODE_READY/$$NODE_COUNT nodes ready)"; \
		fi; \
		kubectl get nodes --no-headers 2>/dev/null | awk "{printf \"    %s: %s  (%s, %s)\n\", \$$1, \$$2, \$$4, \$$5}"; \
		echo ""; \
		\
		if ! kubectl get namespace $(NAMESPACE) >/dev/null 2>&1; then \
			echo "Platform: NOT INSTALLED"; \
			echo "  Run: make all"; \
			exit 0; \
		fi; \
		\
		echo "Platform:"; \
		PLATFORM=$$(kubectl get pods -n $(NAMESPACE) --no-headers -o wide 2>/dev/null | grep -E "nodalarc-|nodalpath-|ome-" || true); \
		if [ -z "$$PLATFORM" ]; then \
			echo "  NOT RUNNING"; \
			echo "  Run: sudo make install"; \
		else \
			TOTAL=$$(echo "$$PLATFORM" | wc -l); \
			RUNNING=$$(echo "$$PLATFORM" | grep -c Running || true); \
			if [ "$$RUNNING" -eq "$$TOTAL" ]; then \
				echo "  Running ($$RUNNING/$$TOTAL platform pods)"; \
			else \
				echo "  DEGRADED ($$RUNNING/$$TOTAL platform pods running)"; \
			fi; \
			echo "$$PLATFORM" | awk "{printf \"    %-45s %-10s %s\n\", \$$1, \$$3, \$$7}"; \
		fi; \
		echo ""; \
		\
		echo "Services:"; \
		VF_PORT=$$(kubectl get svc -n $(NAMESPACE) --no-headers 2>/dev/null | grep "nodalarc-vf" | grep -oP "\\d+:30\\d+" | head -1 || true); \
		API_PORT=$$(kubectl get pods -n $(NAMESPACE) --no-headers -o wide 2>/dev/null | grep "vs-api" | awk "{print \$$6}" | head -1 || true); \
		echo "  Visualization:  http://localhost:3000"; \
		echo "  VS-API:         http://localhost:8080"; \
		echo ""; \
		\
		echo "Session:"; \
		SESSION=$$(kubectl get constellationspec current-session -n $(NAMESPACE) -o json 2>/dev/null); \
		if [ -z "$$SESSION" ] || [ "$$SESSION" = "" ]; then \
			echo "  No session deployed"; \
			echo "  Run: sudo make session"; \
		else \
			SESSION_NAME=$$(echo "$$SESSION" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get(\"status\",{}).get(\"sessionId\",\"unknown\"))" 2>/dev/null); \
			PHASE=$$(echo "$$SESSION" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get(\"status\",{}).get(\"phase\",\"Unknown\"))" 2>/dev/null); \
			WIRED=$$(echo "$$SESSION" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get(\"status\",{}).get(\"wiredPods\",0))" 2>/dev/null); \
			SATS=$$(kubectl get pods -n $(NAMESPACE) -l nodalarc.io/role=satellite --no-headers 2>/dev/null | grep -c Running || echo 0); \
			GS=$$(kubectl get pods -n $(NAMESPACE) -l nodalarc.io/role=ground-station --no-headers 2>/dev/null | grep -c Running || echo 0); \
			echo "  Name: $$SESSION_NAME"; \
			echo "  Phase: $$PHASE"; \
			echo "  Satellites: $$SATS running"; \
			echo "  Ground stations: $$GS running"; \
			echo "  Wired: $$WIRED nodes"; \
			NOT_RUNNING=$$(kubectl get pods -n $(NAMESPACE) -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -v Running | grep -v Completed || true); \
			if [ -n "$$NOT_RUNNING" ]; then \
				echo "  WARNING: Some session pods not running:"; \
				echo "$$NOT_RUNNING" | awk "{printf \"    %s: %s\n\", \$$1, \$$3}"; \
			fi; \
		fi; \
		echo ""; \
		\
		echo "Pod Distribution:"; \
		kubectl get pods -n $(NAMESPACE) -l nodalarc.io/node-id -o wide --no-headers 2>/dev/null | \
			awk "{nodes[\$$7]++} END {for (n in nodes) printf \"  %s: %d session pods\n\", n, nodes[n]}" 2>/dev/null || echo "  No session pods"; \
		echo ""; \
		\
		echo "Links:"; \
		TOKEN=$$(curl -s http://localhost:8080/api/v1/auth/token 2>/dev/null | python3 -c "import json,sys; print(json.load(sys.stdin).get(\"token\",\"\"))" 2>/dev/null || true); \
		if [ -n "$$TOKEN" ]; then \
			curl -s -H "Authorization: Bearer $$TOKEN" http://localhost:8080/api/v1/state 2>/dev/null | \
				python3 -c "import json,sys; s=json.load(sys.stdin); \
					intra=sum(1 for l in s[\"links\"] if l.get(\"link_type\")==\"intra_plane_isl\"); \
					cross=sum(1 for l in s[\"links\"] if l.get(\"link_type\")==\"cross_plane_isl\"); \
					gnd=sum(1 for l in s[\"links\"] if l.get(\"link_type\")==\"ground\"); \
					print(f\"  Intra-plane ISL: {intra}\"); \
					print(f\"  Cross-plane ISL: {cross}\"); \
					print(f\"  Ground links: {gnd}\"); \
					print(f\"  Total active: {len(s[\"links\"])}\")" 2>/dev/null || echo "  Unable to query VS-API"; \
		else \
			echo "  VS-API not reachable"; \
		fi'

test: ## Run unit tests (868+, no sudo needed)
	uv run pytest --ignore=tests/integration --tb=short -q

test-integration: ## Run integration tests (requires running cluster)
	uv run pytest tests/integration --tb=short -q

teardown: ## Full teardown — pods, namespace, cluster resources, kernel state
	bash tools/na-teardown.sh

clean: ## Remove build artifacts (dist/, caches)
	rm -rf frontend/dist nodalpath/console/frontend/dist
	find . -type d -name __pycache__ ! -path ./.venv/\* -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
	@echo "[clean] Build artifacts removed."

clean-deps: ## Remove installed dependencies (.venv, node_modules)
	rm -rf .venv lib/nodalarc.egg-info
	rm -rf frontend/node_modules nodalpath/console/frontend/node_modules
	@echo "[clean-deps] Dependencies removed."

clean-images: ## Remove all nodalarc Docker images
	@docker images --format '{{.Repository}}:{{.Tag}}' | grep -E 'nodalarc|localhost:5000/nodalarc' | \
		xargs -r docker rmi -f 2>/dev/null || true
	@docker builder prune -af 2>/dev/null | tail -1
	@echo "[clean-images] Docker images removed."

clean-registry: ## Purge all images from local container registry and K3s containerd on ALL nodes
	@echo "[clean-registry] Purging local registry and K3s containerd cache..."
	@if docker ps --format '{{.Names}}' | grep -q registry; then \
		echo "  Stopping local registry..."; \
		docker stop registry 2>/dev/null || true; \
		docker rm registry 2>/dev/null || true; \
		docker volume rm registry_data 2>/dev/null || true; \
		echo "  Starting fresh registry..."; \
		docker run -d --restart=always -p 5000:5000 --name registry \
			-v registry_data:/var/lib/registry registry:2 2>/dev/null || true; \
	fi
	@echo "  Purging K3s containerd nodalarc images on ALL nodes..."
	@NA_PODS=$$(kubectl get pods -n $(NAMESPACE) -l app=nodalarc-node-agent \
		--no-headers -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName 2>/dev/null || true); \
	if [ -n "$$NA_PODS" ]; then \
		echo "$$NA_PODS" | while IFS= read -r line; do \
			POD=$$(echo "$$line" | awk '{print $$1}'); \
			NODE=$$(echo "$$line" | awk '{print $$2}'); \
			echo "  Purging containerd on $$NODE via $$POD..."; \
			kubectl exec "$$POD" -n $(NAMESPACE) -c node-agent -- \
				nsenter --target 1 --mount --uts --ipc --pid -- \
				sh -c 'for img in $$(k3s crictl images -q 2>/dev/null | grep nodalarc || true); do k3s crictl rmi "$$img" 2>/dev/null; done' \
				2>/dev/null || echo "  WARNING: purge failed on $$NODE (non-fatal)"; \
		done; \
	fi
	@echo "  Purging local K3s containerd..."
	@for img in $$($(SUDO_CTR) k3s ctr images ls -q 2>/dev/null | grep nodalarc || true); do \
		$(SUDO_CTR) k3s ctr images rm "$$img" 2>/dev/null || true; \
	done
	@echo "[clean-registry] Registry and containerd cache purged on all nodes."

nuke: teardown clean clean-images clean-registry clean-deps ## Remove everything — teardown + images + registry + deps + artifacts
	@echo ""
	@echo "=== Nuke complete. Fresh slate. ==="
