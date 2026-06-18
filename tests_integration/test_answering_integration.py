"""End-to-end retrieval/answer scenarios against the real OpenAI API.

Each scenario checks that the bot finds an answer and cites the expected kind of
source. Source labels: ``faq``, ``course-repo`` (the course's GitHub repo), and
``docs`` (general DataTalks.Club docs + course-specific course pages).

Scenarios:
  llm-zoomcamp  : answer in docs / course-repo (lessons) / faq
  de-zoomcamp   : answer in docs / course-repo (lessons) / faq
  outside course: docs only
"""

from conftest import source_labels

LLM = "llm-zoomcamp"
DE = "data-engineering-zoomcamp"

META_PHRASES = ["the docs say", "the context", "according to the context", "the documentation says"]


def _assert_answered(response):
    assert response["found_answer"] is True, response["answer"]
    assert response["sources"], "expected cited sources"


# --- llm-zoomcamp ----------------------------------------------------------

def test_llm_answer_in_docs(ask):
    r = ask("What are the prerequisites for the LLM Zoomcamp?", "course", LLM)
    _assert_answered(r)
    assert "docs" in source_labels(r), source_labels(r)


def test_llm_answer_in_lessons(ask):
    r = ask("What topics and modules does the LLM Zoomcamp cover?", "course", LLM)
    _assert_answered(r)
    assert "course-repo" in source_labels(r), source_labels(r)


def test_llm_answer_in_faq(ask):
    r = ask("How should I start the course and follow the weekly workflow?", "course", LLM)
    _assert_answered(r)
    assert "faq" in source_labels(r), source_labels(r)


# --- data-engineering-zoomcamp ---------------------------------------------

def test_de_answer_in_docs(ask):
    r = ask("What are the prerequisites for the Data Engineering Zoomcamp?", "course", DE)
    _assert_answered(r)
    assert "docs" in source_labels(r), source_labels(r)


def test_de_answer_in_lessons(ask):
    r = ask("What tools and technologies does the Data Engineering Zoomcamp use?", "course", DE)
    _assert_answered(r)
    assert "course-repo" in source_labels(r), source_labels(r)


def test_de_answer_in_faq(ask):
    r = ask("Course: Can I still join the course after the start date?", "course", DE)
    _assert_answered(r)
    assert "faq" in source_labels(r), source_labels(r)


def test_de_retrieval_spans_docs_and_course_repo(retrieve):
    # Syllabus lives in the docs page, homework in the course repo, so this single
    # natural question retrieves from both source kinds. Asserted on retrieval (not
    # the LLM's citations) so it's deterministic.
    labels = retrieve("Where do I find the syllabus and the homework for the Data Engineering Zoomcamp?", "course", DE)
    assert "docs" in labels
    assert "course-repo" in labels


# --- outside a course channel ----------------------------------------------

def test_non_course_uses_docs_only(ask):
    r = ask("How do I join the DataTalks.Club Slack community?", "docs")
    _assert_answered(r)
    assert source_labels(r) == {"docs"}, source_labels(r)


def test_answer_surfaces_concrete_links_not_vague_pointers(ask):
    # The Slack doc contains real links (invite page, the how-to video, channel
    # links); the answer must include one of them, not say "linked there".
    r = ask("How do I join a Slack channel?", "docs")
    _assert_answered(r)
    answer = r["answer"].lower()
    real_links = ("loom.com", "slack.com/help", "datatalks.club/slack", "app.slack.com")
    assert any(link in answer for link in real_links), r["answer"]
    assert "linked there" not in answer
    assert "see the page" not in answer


# --- prompt behaviour ------------------------------------------------------

def test_answer_is_direct_and_cites_sources(ask):
    r = ask("Can I still join the LLM Zoomcamp after it has already started?", "course", LLM)
    _assert_answered(r)
    lowered = r["answer"].lower()
    for phrase in META_PHRASES:
        assert phrase not in lowered, f"answer should not say {phrase!r}: {r['answer']}"
