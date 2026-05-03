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
        teardown restart clean clean-deps clean-images nuke status \
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
	@echo "  make all                               Full pipeline: deps → build → load/install/session"
	@echo "  make deploy-vs-api                     Iterative dev: build VS-API → load → upgrade in-place"
	@echo "  make session DEFAULT_SESSION=configs/sessions/starlink-176-nodalpath.yaml"
	@echo "  make teardown                          Full teardown"
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
	@$(MAKE) status
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
# check-registry — diagnose multi-node registry config
# ---------------------------------------------------------------------------

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

build-images: ensure-base-images _clear-build-cache build-ome build-scheduler build-node-agent \
              build-vs-api build-operator build-vf build-nodalpath

_clear-build-cache:
	@docker builder prune --filter type=source.local -f >/dev/null 2>&1 || true

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
	docker build --build-arg BUILD_HASH=$(GIT_SHA) -t $(IMG_VF) -t $(REGISTRY_PREFIX)nodalarc/vf:latest frontend/

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
# Multi-node: push to container registry (images already tagged with REGISTRY_PREFIX by build).
load:
	@echo "[load] Pushing images to registry $(REGISTRY_PREFIX) (tag=$(TAG))..."
	@for name in nodalarc/frr nodalarc/node-agent nodalarc/ome nodalarc/scheduler \
		nodalarc/vs-api nodalarc/operator nodalarc/vf nodalarc/nodalpath; do \
		for tag in latest $(TAG); do \
			echo "  $(REGISTRY_PREFIX)$$name:$$tag"; \
			docker push $(REGISTRY_PREFIX)$$name:$$tag 2>&1 | tail -1; \
		done; \
	done
	@echo "[load] Done."
endif

# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------

# install waits for EVERY platform Deployment to reach Available AND the
# Node Agent DaemonSet to finish rolling out. Any failure (timeout,
# missing node label, readiness probe failing) surfaces as exit-nonzero
# — no silent "Platform ready" lie. The desired=0 check catches the
# common footgun where no nodes carry the nodalarc.io/node-agent=true
# label and the DaemonSet silently schedules zero pods.
install: ## Helm install/upgrade the platform chart
	@echo "[install] Installing Helm chart..."
	@if helm status nodalarc -n $(NAMESPACE) >/dev/null 2>&1; then \
		echo "[install] Removing existing installation..."; \
		helm uninstall nodalarc -n $(NAMESPACE) 2>/dev/null || true; \
		kubectl delete namespace $(NAMESPACE) --timeout=30s 2>/dev/null || true; \
	fi
	@if kubectl get namespace $(NAMESPACE) 2>/dev/null | grep -q $(NAMESPACE); then \
		echo "[install] Waiting for namespace to terminate (timeout 120s)..."; \
		WAIT=0; \
		while kubectl get namespace $(NAMESPACE) 2>/dev/null | grep -q $(NAMESPACE); do \
			sleep 2; \
			WAIT=$$((WAIT + 2)); \
			printf "\r[install]   Namespace still terminating... (%ds)" $$WAIT; \
			if [ $$WAIT -ge 120 ]; then \
				echo "[install] ERROR: Namespace deletion stuck after $${WAIT}s."; \
				FINALIZERS=$$(kubectl get ns $(NAMESPACE) -o jsonpath='{.spec.finalizers}' 2>/dev/null); \
				if [ -n "$$FINALIZERS" ]; then \
					echo "[install]   Stuck finalizers: $$FINALIZERS"; \
					echo "[install]   Removing finalizers and force-deleting..."; \
					kubectl get ns $(NAMESPACE) -o json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); d['spec']['finalizers']=[]; print(json.dumps(d))" | kubectl replace --raw "/api/v1/namespaces/$(NAMESPACE)/finalize" -f - >/dev/null 2>&1 || true; \
					sleep 5; \
					if kubectl get namespace $(NAMESPACE) 2>/dev/null | grep -q $(NAMESPACE); then \
						echo "[install] ERROR: Namespace still exists after finalizer removal. Manual intervention required."; \
						exit 1; \
					fi; \
					echo "[install]   Namespace deleted after finalizer removal."; \
				else \
					echo "[install] ERROR: Namespace stuck with no finalizers. Manual intervention required."; \
					exit 1; \
				fi; \
			fi; \
		done; \
		echo ""; \
	fi
	@NODAL_NODE=$$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo ""); \
		if [ -n "$$NODAL_NODE" ]; then \
			echo "[install] Auto-detected node: $$NODAL_NODE"; \
			helm install nodalarc deploy/helm --namespace $(NAMESPACE) --create-namespace \
				--set controlPlaneNode=$$NODAL_NODE --set sessionNodeName=$$NODAL_NODE $(HELM_EXTRA_ARGS); \
		else \
			helm install nodalarc deploy/helm --namespace $(NAMESPACE) --create-namespace $(HELM_EXTRA_ARGS); \
		fi
	@echo "[install] Waiting for platform pods (timeout 180s)..."
	@bash -c '\
		WAIT=0; \
		while [ $$WAIT -lt 180 ]; do \
			TOTAL=$$(kubectl get deployments -n $(NAMESPACE) --no-headers 2>/dev/null | wc -l); \
			AVAIL=$$(kubectl get deployments -n $(NAMESPACE) --no-headers 2>/dev/null | awk "{if (\$$4+0 >= 1) c++} END {print c+0}"); \
			DS_DESIRED=$$(kubectl get ds nodalarc-node-agent -n $(NAMESPACE) -o jsonpath="{.status.desiredNumberScheduled}" 2>/dev/null || echo 0); \
			DS_READY=$$(kubectl get ds nodalarc-node-agent -n $(NAMESPACE) -o jsonpath="{.status.numberReady}" 2>/dev/null || echo 0); \
			if [ "$$AVAIL" -eq "$$TOTAL" ] && [ "$$DS_READY" -eq "$$DS_DESIRED" ] && [ "$$DS_DESIRED" -gt 0 ]; then \
				echo ""; \
				echo "[install] Platform ready: $$TOTAL deployments available, $$DS_READY/$$DS_DESIRED Node Agent pods running."; \
				exit 0; \
			fi; \
			sleep 2; \
			WAIT=$$((WAIT + 2)); \
			printf "\r[install]   Deployments: $$AVAIL/$$TOTAL available, Node Agents: $$DS_READY/$$DS_DESIRED ready (%ds/180s)" $$WAIT; \
		done; \
		echo ""; \
		if [ "$${DS_DESIRED:-0}" = "0" ]; then \
			echo "[install] ERROR: Node Agent DaemonSet has 0 desired pods."; \
			echo "  No nodes carry the nodalarc.io/node-agent=true label."; \
			echo "  Fix: kubectl label nodes --all nodalarc.io/node-agent=true"; \
		else \
			echo "[install] ERROR: Platform pods not ready after 180s."; \
			kubectl get pods -n $(NAMESPACE) --no-headers 2>/dev/null | grep -v Running | grep -v Completed; \
		fi; \
		exit 1'

# ---------------------------------------------------------------------------
# deploy
# ---------------------------------------------------------------------------

session: ## Start a session (DEFAULT_SESSION=path/to/session.yaml)
	@echo "[session] Starting: $(DEFAULT_SESSION)"
	@echo "[session] Waiting for CRD (timeout 60s)..."
	@bash -c '\
		WAIT=0; \
		while ! kubectl get crd constellationspecs.nodalarc.io &>/dev/null; do \
			sleep 2; \
			WAIT=$$((WAIT + 2)); \
			printf "\r[session]   Waiting for Operator to register CRD... (%ds)" $$WAIT; \
			if [ $$WAIT -ge 60 ]; then \
				echo ""; \
				echo "[session] ERROR: CRD not registered after 60s. Is the Operator running?"; \
				exit 1; \
			fi; \
		done; \
		if [ $$WAIT -gt 0 ]; then echo ""; fi'
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
				echo ""; \
				PODS=$$(kubectl get pods -n $(NAMESPACE) -l nodalarc.io/node-id --no-headers 2>/dev/null | wc -l); \
				RUNNING=$$(kubectl get pods -n $(NAMESPACE) -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -c Running || true); \
				NOT_RUNNING=$$(kubectl get pods -n $(NAMESPACE) -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -v Running | grep -v Completed || true); \
				if [ -n "$$NOT_RUNNING" ]; then \
					echo "[session] WARNING: Phase is Ready but some session pods are not running:"; \
					echo "$$NOT_RUNNING"; \
					echo "[session] $$RUNNING/$$PODS session pods running. Check pod status."; \
					exit 1; \
				fi; \
				echo "[session] Session ready. $$RUNNING/$$PODS session pods running."; \
				exit 0; \
			fi; \
			if [ "$$PHASE" = "Error" ]; then \
				echo ""; \
				MSG=$$(kubectl get constellationspec current-session \
					-n $(NAMESPACE) -o jsonpath="{.status.message}" 2>/dev/null); \
				echo "[session] ERROR: $$MSG"; \
				exit 1; \
			fi; \
			sleep 5; \
			ELAPSED=$$((ELAPSED + 5)); \
			PODS=$$(kubectl get pods -n $(NAMESPACE) -l nodalarc.io/node-id --no-headers 2>/dev/null | grep -c Running || true); \
			printf "\r[session]   Phase: $$PHASE, $$PODS session pods running (%ds/300s)" $$ELAPSED; \
		done; \
		echo ""; \
		echo "[session] ERROR: Timed out after 300s"; exit 1'

restart: ## Rolling restart all platform pods (forces image re-pull)
	@echo "[restart] Rolling restart of all platform deployments and daemonsets..."
	@for dep in ome nodalarc-scheduler nodalarc-vs-api nodalarc-operator nodalarc-vf nodalpath; do \
		kubectl rollout restart deployment/$$dep -n $(NAMESPACE) 2>/dev/null && \
			echo "  Restarted deployment/$$dep" || true; \
	done
	@kubectl rollout restart daemonset/nodalarc-node-agent -n $(NAMESPACE) 2>/dev/null && \
		echo "  Restarted daemonset/nodalarc-node-agent" || true
	@echo "[restart] Waiting for rollout..."
	@for dep in ome nodalarc-scheduler nodalarc-vs-api nodalarc-operator nodalarc-vf nodalpath; do \
		kubectl rollout status deployment/$$dep -n $(NAMESPACE) --timeout=60s 2>/dev/null || true; \
	done
	@kubectl rollout status daemonset/nodalarc-node-agent -n $(NAMESPACE) --timeout=60s 2>/dev/null || true
	@echo "[restart] Done."

# ---------------------------------------------------------------------------
# upgrade — in-place Helm upgrade without teardown
# ---------------------------------------------------------------------------
#
# Updates image tags and Helm values via `helm upgrade --install` without
# tearing down the namespace. Use after committing to update the Helm
# release with the new git SHA tags. Session pods are not affected.
#
# Workflow:
#   git commit → make build && make load && make upgrade

upgrade: ## In-place Helm upgrade (updates image tags, no teardown)
	@echo "[upgrade] Upgrading Helm release..."
	@NODAL_NODE=$$(kubectl get nodes -o jsonpath='{.items[0].metadata.name}' 2>/dev/null || echo ""); \
		if [ -n "$$NODAL_NODE" ]; then \
			echo "[upgrade] Auto-detected node: $$NODAL_NODE"; \
			helm upgrade --install nodalarc deploy/helm --namespace $(NAMESPACE) --create-namespace \
				--set controlPlaneNode=$$NODAL_NODE --set sessionNodeName=$$NODAL_NODE $(HELM_EXTRA_ARGS); \
		else \
			helm upgrade --install nodalarc deploy/helm --namespace $(NAMESPACE) --create-namespace $(HELM_EXTRA_ARGS); \
		fi
	@echo "[upgrade] Waiting for platform pods (timeout 120s)..."
	@bash -c '\
		WAIT=0; \
		while [ $$WAIT -lt 120 ]; do \
			TOTAL=$$(kubectl get deployments -n $(NAMESPACE) --no-headers 2>/dev/null | wc -l); \
			AVAIL=$$(kubectl get deployments -n $(NAMESPACE) --no-headers 2>/dev/null | awk "{if (\$$4+0 >= 1) c++} END {print c+0}"); \
			DS_DESIRED=$$(kubectl get ds nodalarc-node-agent -n $(NAMESPACE) -o jsonpath="{.status.desiredNumberScheduled}" 2>/dev/null || echo 0); \
			DS_READY=$$(kubectl get ds nodalarc-node-agent -n $(NAMESPACE) -o jsonpath="{.status.numberReady}" 2>/dev/null || echo 0); \
			if [ "$$AVAIL" -eq "$$TOTAL" ] && [ "$$DS_READY" -eq "$$DS_DESIRED" ] && [ "$$DS_DESIRED" -gt 0 ]; then \
				echo ""; \
				echo "[upgrade] Platform ready: $$TOTAL deployments available, $$DS_READY/$$DS_DESIRED Node Agent pods running."; \
				exit 0; \
			fi; \
			sleep 2; \
			WAIT=$$((WAIT + 2)); \
			printf "\r[upgrade]   Deployments: $$AVAIL/$$TOTAL available, Node Agents: $$DS_READY/$$DS_DESIRED ready (%ds/120s)" $$WAIT; \
		done; \
		echo ""; \
		echo "[upgrade] ERROR: Platform pods not ready after 120s."; \
		kubectl get pods -n $(NAMESPACE) --no-headers 2>/dev/null | grep -v Running | grep -v Completed; \
		exit 1'

# ---------------------------------------------------------------------------
# deploy-* — build one service, push to registry, rollout restart
# ---------------------------------------------------------------------------
#
# Iterative dev loop — no commit needed:
#   edit code → make deploy-vs-api → test in browser → repeat
#
# How it works:
#   1. Build the one service image (Docker layer cache makes unchanged
#      layers instant — only the COPY layer with your code change rebuilds)
#   2. Push BOTH :SHA and :latest tags to the registry. The :SHA tag
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

ifeq ($(REGISTRY_PREFIX),)
define _load-image
	@docker save $1 | $(SUDO_CTR) k3s ctr images import - 2>&1 | tail -1
	@docker save $(subst :$(TAG),:latest,$1) | $(SUDO_CTR) k3s ctr images import - 2>&1 | tail -1
endef
else
define _load-image
	@docker push $1 2>&1 | tail -1
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
	@KUBECONFIG=$(KUBECONFIG) NAMESPACE=$(NAMESPACE) REGISTRY_HOST=$(REGISTRY_HOST) DEFAULT_SESSION=$(DEFAULT_SESSION) bash tools/na-status.sh

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
	@docker images --format '{{.Repository}}:{{.Tag}}' | grep -E 'nodalarc/' | \
		xargs -r docker rmi -f 2>/dev/null || true
	@docker builder prune -af 2>/dev/null | tail -1
	@echo "[clean-images] Docker images removed."

# clean-registry does two things:
#   1. Remote purge of nodalarc/* from REGISTRY_HOST via Registry V2 API (crane).
#      Works for any OCI-compliant registry — CNCF distribution, Harbor, GHCR,
#      ECR, etc. See tools/clean-registry.sh for single-node + HTTP-vs-HTTPS
#      + dep handling. No-ops cleanly when REGISTRY_HOST is empty.
#   2. K3s containerd per-node image cache purge — via the running node-agent
#      DaemonSet pods (kubectl exec + nsenter + k3s crictl rmi). Requires the
#      platform to be installed; no-ops cleanly when it's not.
clean-registry: ## Purge nodalarc images from REGISTRY_HOST and K3s containerd cache on all nodes
	@REGISTRY_HOST='$(REGISTRY_HOST)' tools/clean-registry.sh
	@echo "[clean-registry] Purging K3s containerd nodalarc images on all nodes..."
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
	else \
		echo "  (node-agent pods not running — skipping containerd purge)"; \
	fi
	@# Build host's own K3s containerd (for single-node dev where host == node).
	@echo "  Purging local K3s containerd..."
	@for img in $$($(SUDO_CTR) k3s ctr images ls -q 2>/dev/null | grep nodalarc || true); do \
		$(SUDO_CTR) k3s ctr images rm "$$img" 2>/dev/null || true; \
	done
	@echo "[clean-registry] Done."

# Ordering matters: clean-registry needs node-agent pods alive for the
# per-node containerd purge step, so it must run BEFORE teardown.
nuke: ## Remove everything — registry + teardown + images + deps + artifacts
	$(MAKE) clean-registry
	$(MAKE) teardown
	$(MAKE) clean
	$(MAKE) clean-images
	$(MAKE) clean-deps
	@echo ""
	@echo "=== Nuke complete. Fresh slate. ==="
