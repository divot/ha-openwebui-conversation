"""Tests for the content-redacting live contract probe."""

from scripts.probe_openwebui_contract import _matches_expectation


def test_search_expectation_requires_final_text_and_retrieval_evidence() -> None:
    """Unsupported prose must not be mistaken for a successful web search."""
    assert not _matches_expectation(
        {"has_final_text": True, "has_sources": False},
        "search",
    )
    assert not _matches_expectation(
        {"has_final_text": False, "has_sources": True},
        "search",
    )


def test_search_expectation_accepts_json_sources_or_stream_source_events() -> None:
    """Both response protocols can prove that web retrieval ran."""
    assert _matches_expectation(
        {"has_final_text": True, "has_sources": True},
        "search",
    )
    assert _matches_expectation(
        {"has_final_text": True, "source_events": 1},
        "search",
    )
