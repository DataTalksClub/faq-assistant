.PHONY: sync config check ingest index-create

sync:
	uv sync

config:
	uv run python scripts/compile_config.py

check:
	uv run python scripts/compile_config.py
	uv run python scripts/check_structured_parsing.py
	uv run python -m compileall src scripts

index-create:
	uv run --group ingest faq-assistant index create

ingest:
	uv run --group ingest faq-assistant ingest --mode rebuild
