"""하이브리드(밀집+희소 RRF) 위키 검색."""
import embeddings


def search_wiki(question: str, limit: int = 5) -> list[dict]:
    from qdrant_client import models

    vec = embeddings.encode([question])[0]
    client = embeddings.qdrant()
    res = client.query_points(
        collection_name=embeddings.COLLECTION,
        prefetch=[
            models.Prefetch(query=vec["dense"], using="dense", limit=20),
            models.Prefetch(
                query=models.SparseVector(
                    indices=list(vec["sparse"].keys()), values=list(vec["sparse"].values())
                ),
                using="sparse", limit=20,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=limit,
        with_payload=True,
    )
    return [
        {"path": p.payload["path"], "text": p.payload["text"], "score": p.score}
        for p in res.points
    ]
