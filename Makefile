CONSOLE_FRONTEND := nodalpath/console/frontend
CONSOLE_DIST     := $(CONSOLE_FRONTEND)/dist

.PHONY: build-nodalpath-console
build-nodalpath-console:
	@echo "Building NodalPath console frontend..."
	cd $(CONSOLE_FRONTEND) && npm install && npm run build
	@echo "Done. Output: $(CONSOLE_DIST)"

.PHONY: clean-nodalpath-console
clean-nodalpath-console:
	rm -rf $(CONSOLE_DIST)
