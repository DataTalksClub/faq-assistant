.PHONY: sync config check ingest index-create

sync:
	uv sync

config:
	uv run python scripts/compile_config.py

check:
	uv run python scripts/compile_config.py
	uv run python -m compileall src scripts

index-create:
	uv run faq-assistant index create

ingest:
	uv run faq-assistant ingest --mode rebuild
