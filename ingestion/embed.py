"""
Phase 2.7 — Vector Embedding & Store (ChromaDB + BGE-small)

Reads `data/chunks.json` (45 section-level chunks produced by chunk.py) and
builds a *persistent* ChromaDB collection of their embeddings, so the backend
RAG engine can perform semantic retrieval with metadata pre-filtering.

Design decisions (see Docs/implementation-plan.md):
  * Embedding model : BAAI/bge-small-en-v1.5 (384-dim, ~130 MB, local, free).
                      Swap EMBED_MODEL below for bge-base / bge-large if needed.
  * Store           : ChromaDB PersistentClient at data/chroma/.
  * Metadata        : fund_key, fund_name, section, source_url — enables hybrid
                      retrieval (filter by fund/section, then rank by cosine).
  * id              : chunk_id ("{fund_key}::{section}") — stable + idempotent,
                      so re-running upserts rather than duplicating.

The model is downloaded once on first run and cached locally by
sentence-transformers; subsequent runs are offline-capable.
"""

from __future__ import annotations

import json
from pathlib import Path

# --- Paths -----------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
CHUNKS_PATH = ROOT / "data" / "chunks.json"
CHROMA_DIR = ROOT / "data" / "chroma"
COLLECTION_NAME = "fund_chunks"

# BGE requires a retrieval instruction prefix on *queries* (not documents) for
# best results. The backend must apply this same prefix when embedding a user
# query. Exposed here so both sides stay in sync.
EMBED_MODEL = "BAAI/bge-small-en-v1.5"
QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def load_chunks() -> dict:
    """Load chunks.json, dropping the non-chunk `_meta` housekeeping entry."""
    if not CHUNKS_PATH.exists():
        raise FileNotFoundError(
            f"{CHUNKS_PATH} not found — run the ingestion pipeline (chunk.py) first."
        )
    raw = json.loads(CHUNKS_PATH.read_text(encoding="utf-8"))
    meta = raw.get("_meta", {})
    chunks = {cid: c for cid, c in raw.items() if cid != "_meta"}
    return chunks, meta


def _embedding_function():
    """Build Chroma's SentenceTransformer embedding function for BGE-small."""
    from chromadb.utils import embedding_functions

    return embedding_functions.SentenceTransformerEmbeddingFunction(
        model_name=EMBED_MODEL,
    )


def build_store(verbose: bool = True) -> int:
    """(Re)build the persistent Chroma collection from chunks.json.

    Returns the number of chunks indexed. Idempotent: existing ids are
    overwritten via upsert, so a daily pipeline run keeps the store fresh
    without duplicating vectors.
    """
    import chromadb

    chunks, meta = load_chunks()
    if not chunks:
        raise ValueError("No chunks to embed — chunks.json contained only _meta.")

    CHROMA_DIR.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))

    # get_or_create so reruns reuse the same collection; embedding fn is bound
    # to the collection so queries are embedded with the identical model.
    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=_embedding_function(),
        metadata={"hnsw:space": "cosine", "embed_model": EMBED_MODEL},
    )

    ids, documents, metadatas = [], [], []
    for cid, c in chunks.items():
        text = (c.get("text") or "").strip()
        if not text:
            if verbose:
                print(f"  ! skipping {cid}: empty text field")
            continue
        ids.append(cid)
        documents.append(text)
        metadatas.append(
            {
                "fund_key": c.get("fund_key") or "",
                "fund_name": c.get("fund_name") or "",
                "section": c.get("section") or "",
                "source_url": c.get("source_url") or "",
            }
        )

    # upsert = insert-or-replace by id → safe to re-run after each ingestion.
    collection.upsert(ids=ids, documents=documents, metadatas=metadatas)

    if verbose:
        print(f"✓ Embedded {len(ids)} chunks → {CHROMA_DIR}")
        print(f"  collection='{COLLECTION_NAME}'  model='{EMBED_MODEL}'")
        print(f"  corpus built_at={meta.get('built_at', 'unknown')}")
        print(f"  collection count now = {collection.count()}")
    return len(ids)


def query(text: str, n_results: int = 3, fund_key: str | None = None,
          section: str | None = None) -> dict:
    """Convenience semantic search used for smoke-testing the store.

    The backend will use the same pattern: optional metadata `where` filter
    (hybrid) + BGE query instruction prefix + top-k cosine.
    """
    import chromadb

    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.get_collection(
        name=COLLECTION_NAME, embedding_function=_embedding_function()
    )

    where = {}
    if fund_key:
        where["fund_key"] = fund_key
    if section:
        where["section"] = section

    return collection.query(
        query_texts=[QUERY_INSTRUCTION + text],
        n_results=n_results,
        where=where or None,
    )


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "query":
        q = sys.argv[2] if len(sys.argv) > 2 else "What is the exit load?"
        res = query(q, n_results=3)
        print(f"\nQuery: {q!r}")
        for cid, doc, dist in zip(
            res["ids"][0], res["documents"][0], res["distances"][0]
        ):
            print(f"\n  [{dist:.4f}] {cid}\n    {doc[:160]}")
    else:
        build_store()
