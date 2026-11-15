from retrieval.bm25 import BM25Index


def test_title_recall_keeps_original_relevance_scores():
    chunks = [{"id": "a", "title": "key", "text": "key " + "filler " * 50},
              {"id": "b", "title": "other", "text": "key key key"}]
    index = BM25Index.build(chunks)
    baseline = {x["id"]: x["score"] for x in index.search("key", 2)}
    candidate = index.search_with_titles("key", 2)
    assert {x["id"] for x in candidate} == set(baseline)
    assert all(x["score"] == baseline[x["id"]] for x in candidate)


def test_missing_titles_preserve_ranking_and_empty_query_stays_empty():
    index = BM25Index.build([{"id": "a", "text": "alpha"}, {"id": "b", "text": "beta"}])
    assert index.search_with_titles("alpha", 5) == index.search("alpha", 5)
    assert index.search_with_titles("unseen", 5) == []


def test_title_only_match_cannot_bypass_content_relevance():
    index = BM25Index.build([{"id": "a", "title": "alpha", "text": "beta"}])
    assert index.search_with_titles("alpha", 5) == []


def test_title_lane_can_rescue_a_content_match_outside_top_five():
    chunks = [{"id": str(i), "title": "other", "text": "topic"} for i in range(7)]
    chunks.append({"id": "target", "title": "topic", "text": "topic " + "noise " * 100})
    index = BM25Index.build(chunks)
    original = index.search("topic", 5)
    assert "target" not in {x["id"] for x in original}
    assert "target" in {x["id"] for x in index.search_with_titles("topic", 5)}
    assert index.search("topic", 5) == original
