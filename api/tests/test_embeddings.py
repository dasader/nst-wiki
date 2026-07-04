import embeddings


def test_chunk_page_splits_by_heading():
    text = "# 제목\n\n서론입니다.\n\n## 배경\n\n" + "가" * 100 + "\n\n## 현황\n\n나나나"
    chunks = embeddings.chunk_page(text)
    assert len(chunks) == 3
    assert chunks[1].startswith("## 배경")


def test_chunk_page_splits_long_sections():
    text = "## 긴절\n\n" + ("문단입니다. " * 40 + "\n\n") * 5  # 한 절이 max_chars 초과
    chunks = embeddings.chunk_page(text, max_chars=500)
    assert len(chunks) > 1
    assert all(len(c) <= 600 for c in chunks)  # 문단 경계라 약간 여유


def test_index_page_deletes_then_upserts(monkeypatch):
    calls = []
    monkeypatch.setattr(embeddings, "encode",
                        lambda texts: [{"dense": [0.0] * 1024, "sparse": {1: 0.5}} for _ in texts])

    class FakeClient:
        def delete(self, collection_name, points_selector):
            calls.append(("delete", collection_name))

        def upsert(self, collection_name, points):
            calls.append(("upsert", collection_name, len(points)))

    embeddings.index_page(FakeClient(), "tech/a.md", "# A\n\n본문\n\n## 절\n\n내용")
    assert calls[0][0] == "delete"
    assert calls[1][0] == "upsert"
    assert calls[1][2] >= 1
