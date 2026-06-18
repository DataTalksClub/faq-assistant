"""Shared fixtures for the unit tests.

Fully offline: the OpenAI chat and the search index are mocked, so nothing here
hits the network or needs a key.
"""

import copy
import json
from unittest.mock import Mock

import pytest

from faq_assistant.generated_config import CONFIG as _CONFIG
from faq_assistant.models import QueryRewrite


@pytest.fixture
def cfg():
    # Real config, but drop the min-score floor so tiny/mocked results score through.
    config = copy.deepcopy(_CONFIG)
    config["retrieval"]["min_score"] = 0.0
    return config


def mock_chat(*, rewrite="rewritten", answer="Here you go.", found_answer=True, source_ids=None):
    """A Mock chat callable returning canned structured responses (no network)."""
    sids = source_ids or []

    def _impl(messages, output_model, max_tokens, temperature, model=None):
        if output_model is QueryRewrite:
            content = {"query": rewrite}
        else:
            content = {"answer": answer, "found_answer": found_answer, "source_ids": sids}
        return {
            "choices": [{"message": {"content": json.dumps(content)}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
        }

    return Mock(side_effect=_impl)


def record(**overrides):
    """A search-result record dict as zerosearch.Index.search would return."""
    base = {
        "id": "", "score": 1.0, "source_type": "faq", "scope": "course", "course": "",
        "section": "", "title": "", "text": "", "url": "", "repo": "", "path": "",
    }
    base.update(overrides)
    return base


def mock_index(records=()):
    """A Mock index whose .search() returns the given records."""
    index = Mock()
    index.search = Mock(return_value=[dict(r) for r in records])
    return index
