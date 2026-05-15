from __future__ import annotations

from difflib import SequenceMatcher

from magnet_search.models import SearchResult


PROVIDER_BOOSTS = {
    "internet_archive": 0.1,
}


def _text_similarity(query: str, title: str) -> float:
    query_text = query.casefold().strip()
    title_text = title.casefold().strip()
    if not query_text or not title_text:
        return 0.0
    return SequenceMatcher(None, query_text, title_text).ratio()


def score_result(query: str, result: SearchResult) -> float:
    score = _text_similarity(query, result.title)
    if result.magnet:
        score += 0.25
    if result.url:
        score += 0.03
    if result.size:
        score += 0.03
    if result.published_at:
        score += 0.03
    score += PROVIDER_BOOSTS.get(result.source, 0.0)
    return score


def rank_results(query: str, results: list[SearchResult], limit: int) -> list[SearchResult]:
    if limit < 0:
        raise ValueError("limit must be non-negative")

    scored = []
    for result in results:
        raw_score = score_result(query, result)
        scored.append((raw_score, result.with_score(raw_score)))
    scored.sort(
        key=lambda item: (
            -item[0],
            item[1].title.casefold(),
            item[1].source.casefold(),
            item[1].magnet,
            item[1].url,
            item[1].size.casefold(),
            item[1].published_at,
            item[1].query.casefold(),
        )
    )
    return [result for _, result in scored[:limit]]
