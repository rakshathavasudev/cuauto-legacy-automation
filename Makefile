.PHONY: install mocks stop test demo demo-offline console
install:      ; pip install -e '.[dev]' && playwright install chromium
mocks:        ; scripts/mocks.sh start
stop:         ; scripts/mocks.sh stop
test:         ; pytest -q
demo:         ; scripts/demo.sh
demo-offline: ; OFFLINE=1 scripts/demo.sh
console:      ; cuauto operator console
