import search


def test_search_wiki_fuses_and_formats(monkeypatch):
    import embeddings
    monkeypatch.setattr(embeddings, "encode",
                        lambda texts: [{"dense": [0.0] * 1024, "sparse": {3: 1.0}}])

    class P:
        def __init__(self, path, text, score):
            self.payload = {"path": path, "text": text}
            self.score = score

    class FakeClient:
        def query_points(self, collection_name, prefetch, query, limit, with_payload):
            assert collection_name == "wiki_pages"
            assert len(prefetch) == 2
            class R: points = [P("tech/a.md", "본문", 0.9)]
            return R()

    monkeypatch.setattr(embeddings, "qdrant", lambda: FakeClient())
    out = search.search_wiki("HBM이 뭐야?")
    assert out == [{"path": "tech/a.md", "text": "본문", "score": 0.9}]
