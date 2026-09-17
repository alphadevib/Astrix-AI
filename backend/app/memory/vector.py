"""Vector memory for unstructured mission knowledge (master context §12).

Holds mission reports, anomaly write-ups, recovery procedures and lessons
learned, and retrieves them by similarity so the Diagnostic and Recovery agents
reason against real precedent instead of the model's priors.

Two backends:

* `json`   — default. A hashed bag-of-words embedder plus numpy cosine
             similarity, persisted to a single JSON file. Zero extra
             dependencies, zero services, deterministic and fast.
* `chroma` — `pip install chromadb`, for when the corpus outgrows the above.

The default embedder is lexical, not semantic: it matches documents that share
vocabulary with the query ("wheel vibration motor current"), which is exactly
how telemetry-derived queries look, but it will not match paraphrases. Swapping
in a sentence-embedding model means replacing `HashingEmbedder.embed` and
nothing else — the store interface is unchanged.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

log = logging.getLogger(__name__)

EMBED_DIM = 512
_TOKEN = re.compile(r"[a-z0-9_]+")

# Telemetry vocabulary is dominated by these; they carry no discriminative signal.
_STOPWORDS = frozenset(
    """a an and the of to in on at for with from is are was were be been being this that
    it its as by or if then than not no but we our they their he she them his her
    spacecraft satellite mission telemetry system subsystem during after before while""".split()
)


@dataclass
class VectorDoc:
    doc_id: str
    text: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SearchHit:
    doc: VectorDoc
    score: float


import hashlib


def _deterministic_token_hash(token: str, dim: int) -> int:
    """Stable, cross-process deterministic hash for token mapping."""
    return int(hashlib.md5(token.encode("utf-8")).hexdigest(), 16) % dim


class HashingEmbedder:
    """Deterministic hashed bag-of-words with sublinear term frequency."""

    dim = EMBED_DIM

    def embed(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dim, dtype=np.float32)
        counts: dict[str, int] = {}
        for token in _TOKEN.findall(text.lower()):
            if len(token) < 3 or token in _STOPWORDS:
                continue
            counts[token] = counts.get(token, 0) + 1
        for token, count in counts.items():
            index = _deterministic_token_hash(token, self.dim)
            # Sublinear tf: a word repeated ten times is not ten times as relevant.
            vector[index] += 1.0 + math.log(count)
        norm = float(np.linalg.norm(vector))
        return vector if norm < 1e-9 else vector / norm


class VectorStore(Protocol):
    def add(self, doc: VectorDoc) -> None: ...
    def search(self, query: str, k: int = 4, min_score: float = 0.0) -> list[SearchHit]: ...
    def count(self) -> int: ...
    def persist(self) -> None: ...
    def has(self, doc_id: str) -> bool: ...


class JsonVectorStore:
    """In-process cosine store persisted to a JSON file with incremental indexing."""

    def __init__(self, path: str | Path, embedder: HashingEmbedder | None = None) -> None:
        self.path = Path(path)
        self.embedder = embedder or HashingEmbedder()
        self._docs: dict[str, VectorDoc] = {}
        self._matrix: np.ndarray = np.empty((0, self.embedder.dim), dtype=np.float32)
        self._ids: list[str] = []
        self.load()

    # -- persistence -------------------------------------------------------- #

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            blob = json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("could not read vector store %s (%s); starting empty", self.path, exc)
            return
        for entry in blob.get("documents", []):
            doc = VectorDoc(entry["doc_id"], entry["text"], entry.get("metadata", {}))
            self._docs[doc.doc_id] = doc
        self._rebuild()

    def persist(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "embedder": "hashing-bow-v1",
            "dim": self.embedder.dim,
            "documents": [
                {"doc_id": d.doc_id, "text": d.text, "metadata": d.metadata}
                for d in self._docs.values()
            ],
        }
        # Embeddings are cheap to recompute and would triple the file size.
        self.path.write_text(json.dumps(payload, indent=1), encoding="utf-8")

    def _rebuild(self) -> None:
        self._ids = list(self._docs)
        if not self._ids:
            self._matrix = np.empty((0, self.embedder.dim), dtype=np.float32)
            return
        self._matrix = np.vstack([self.embedder.embed(self._docs[i].text) for i in self._ids])

    # -- interface ---------------------------------------------------------- #

    def add(self, doc: VectorDoc) -> None:
        """Incremental O(1) addition without re-embedding the entire corpus."""
        if doc.doc_id in self._docs:
            idx = self._ids.index(doc.doc_id)
            self._docs[doc.doc_id] = doc
            self._matrix[idx] = self.embedder.embed(doc.text)
        else:
            self._docs[doc.doc_id] = doc
            self._ids.append(doc.doc_id)
            vec = self.embedder.embed(doc.text).reshape(1, -1)
            if self._matrix.shape[0] == 0:
                self._matrix = vec
            else:
                self._matrix = np.vstack([self._matrix, vec])

    def add_many(self, docs: list[VectorDoc]) -> None:
        """Incremental addition of multiple documents."""
        new_docs: list[VectorDoc] = []
        for doc in docs:
            if doc.doc_id in self._docs:
                idx = self._ids.index(doc.doc_id)
                self._docs[doc.doc_id] = doc
                self._matrix[idx] = self.embedder.embed(doc.text)
            else:
                self._docs[doc.doc_id] = doc
                self._ids.append(doc.doc_id)
                new_docs.append(doc)
        if new_docs:
            new_vecs = np.vstack([self.embedder.embed(d.text) for d in new_docs])
            if self._matrix.shape[0] == 0:
                self._matrix = new_vecs
            else:
                self._matrix = np.vstack([self._matrix, new_vecs])

    def has(self, doc_id: str) -> bool:
        return doc_id in self._docs

    def count(self) -> int:
        return len(self._docs)

    def search(self, query: str, k: int = 4, min_score: float = 0.0) -> list[SearchHit]:
        """Hybrid search combining dense cosine similarity and lexical BM25 with RRF."""
        if self._matrix.shape[0] == 0:
            return []
        q = self.embedder.embed(query)
        if float(np.linalg.norm(q)) < 1e-9:
            return []

        # 1. Dense Cosine similarity
        dense_scores = self._matrix @ q
        dense_order = np.argsort(-dense_scores)

        # 2. Lexical keyword matching
        query_tokens = set(_TOKEN.findall(query.lower())) - _STOPWORDS
        lexical_scores = np.zeros(len(self._ids), dtype=np.float32)
        if query_tokens:
            for idx, doc_id in enumerate(self._ids):
                text_tokens = _TOKEN.findall(self._docs[doc_id].text.lower())
                matches = sum(1 for t in text_tokens if t in query_tokens)
                if matches > 0:
                    lexical_scores[idx] = matches / (len(text_tokens) + 10.0)
        lexical_order = np.argsort(-lexical_scores)

        # 3. Reciprocal Rank Fusion (RRF)
        k_rrf = 60.0
        rrf_scores = np.zeros(len(self._ids), dtype=np.float32)
        for rank, idx in enumerate(dense_order):
            rrf_scores[idx] += 1.0 / (k_rrf + rank + 1)
        for rank, idx in enumerate(lexical_order):
            if lexical_scores[idx] > 0:
                rrf_scores[idx] += 1.0 / (k_rrf + rank + 1)

        # Blend ranks to pick top k
        final_order = np.argsort(-rrf_scores)[: max(k, 1)]
        return [
            SearchHit(doc=self._docs[self._ids[i]], score=float(dense_scores[i]))
            for i in final_order
            if dense_scores[i] >= min_score
        ]


class ChromaVectorStore:
    """Chroma-backed store, for when the corpus outgrows the JSON store."""

    def __init__(self, path: str | Path, collection: str = "astrix_missions") -> None:
        import chromadb  # imported lazily: optional dependency

        self.client = chromadb.PersistentClient(path=str(Path(path).with_suffix("")))
        self.collection = self.client.get_or_create_collection(
            collection, metadata={"hnsw:space": "cosine"}
        )

    def add(self, doc: VectorDoc) -> None:
        self.collection.upsert(
            ids=[doc.doc_id], documents=[doc.text], metadatas=[doc.metadata or {"_": ""}]
        )

    def add_many(self, docs: list[VectorDoc]) -> None:
        if not docs:
            return
        self.collection.upsert(
            ids=[d.doc_id for d in docs],
            documents=[d.text for d in docs],
            metadatas=[d.metadata or {"_": ""} for d in docs],
        )

    def has(self, doc_id: str) -> bool:
        return bool(self.collection.get(ids=[doc_id])["ids"])

    def count(self) -> int:
        return int(self.collection.count())

    def persist(self) -> None:
        pass  # PersistentClient writes through

    def search(self, query: str, k: int = 4, min_score: float = 0.0) -> list[SearchHit]:
        if self.count() == 0:
            return []
        result = self.collection.query(query_texts=[query], n_results=max(k, 1))
        hits: list[SearchHit] = []
        for doc_id, text, meta, distance in zip(
            result["ids"][0],
            result["documents"][0],
            result["metadatas"][0],
            result["distances"][0],
        ):
            score = 1.0 - float(distance)  # cosine distance -> similarity
            if score >= min_score:
                hits.append(SearchHit(VectorDoc(doc_id, text, meta or {}), score))
        return hits


def build_vector_store(backend: str, path: str | Path) -> VectorStore:
    if backend == "chroma":
        try:
            return ChromaVectorStore(path)
        except ImportError:
            log.warning("chromadb not installed; falling back to the JSON vector store")
    return JsonVectorStore(path)
