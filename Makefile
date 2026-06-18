.PHONY: sync config corpus index check deploy

sync:
	uv sync

config:
	uv run python scripts/compile_config.py

# Rebuild the search corpus from the sources (needs the `ingest` group).
corpus:
	uv run --group ingest python scripts/build_search_corpus.py

# Fit the search index from the corpus and write the packed .zsx artifact.
index:
	uv run python scripts/build_search_index.py

check:
	uv run python scripts/compile_config.py
	uv run python scripts/check_structured_parsing.py
	uv run python scripts/build_search_index.py
	uv run python scripts/check_handler.py
	uv run python -m compileall -q src scripts

# Assemble the Lambda deployment package. Invoked by SAM (BuildMethod: makefile);
# CWD is the repo root and ARTIFACTS_DIR is provided by `sam build`.
build-FaqWorkerFunction:
	uv pip install --target "$(ARTIFACTS_DIR)" zerosearch==0.2.0
	cp -r src/faq_assistant "$(ARTIFACTS_DIR)/faq_assistant"
	rm -f "$(ARTIFACTS_DIR)/faq_assistant/search_corpus.py"
	cp artifacts/search/search-index.zsx "$(ARTIFACTS_DIR)/search-index.zsx"

# Build the index, package, and deploy. First run: `uv run sam deploy --guided`.
deploy: index
	uv run sam build
	uv run sam deploy
