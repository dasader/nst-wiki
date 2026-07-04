"""BGE-M3 임베딩 + Qdrant 색인. 모델 로드는 지연 — 워커에서만 실체화된다."""
import os
import uuid

COLLECTION = "wiki_pages"
_model = None


def _bge():
    global _model
    if _model is None:
        from FlagEmbedding import BGEM3FlagModel

        _model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=False)
    return _model


def encode(texts: list[str]) -> list[dict]:
    out = _bge().encode(texts, return_dense=True, return_sparse=True)
    return [
        {
            "dense": out["dense_vecs"][i].tolist(),
            "sparse": {int(k): float(v) for k, v in out["lexical_weights"][i].items()},
        }
        for i in range(len(texts))
    ]


def chunk_page(text: str, max_chars: int = 1200) -> list[str]:
    sections, cur = [], []
    for line in text.splitlines():
        if line.startswith("## ") and cur:
            sections.append("\n".join(cur).strip())
            cur = []
        cur.append(line)
    if cur:
        sections.append("\n".join(cur).strip())
    chunks = []
    for sec in sections:
        if len(sec) <= max_chars:
            if sec:
                chunks.append(sec)
            continue
        buf = ""
        for para in sec.split("\n\n"):
            if buf and len(buf) + len(para) > max_chars:
                chunks.append(buf.strip())
                buf = ""
            buf += para + "\n\n"
        if buf.strip():
            chunks.append(buf.strip())
    return chunks


def qdrant():
    from qdrant_client import QdrantClient

    return QdrantClient(url=os.environ.get("QDRANT_URL", "http://localhost:6333"))


def ensure_collection(client) -> None:
    from qdrant_client import models

    if not client.collection_exists(COLLECTION):
        client.create_collection(
            COLLECTION,
            vectors_config={"dense": models.VectorParams(size=1024, distance=models.Distance.COSINE)},
            sparse_vectors_config={"sparse": models.SparseVectorParams()},
        )


def index_page(client, path: str, text: str) -> int:
    from qdrant_client import models

    client.delete(
        collection_name=COLLECTION,
        points_selector=models.FilterSelector(filter=models.Filter(must=[
            models.FieldCondition(key="path", match=models.MatchValue(value=path))
        ])),
    )
    chunks = chunk_page(text)
    if not chunks:
        return 0
    vecs = encode(chunks)
    points = [
        models.PointStruct(
            id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"{path}#{i}")),
            vector={
                "dense": v["dense"],
                "sparse": models.SparseVector(
                    indices=list(v["sparse"].keys()), values=list(v["sparse"].values())
                ),
            },
            payload={"path": path, "chunk": i, "text": chunk},
        )
        for i, (chunk, v) in enumerate(zip(chunks, vecs))
    ]
    client.upsert(collection_name=COLLECTION, points=points)
    return len(points)
