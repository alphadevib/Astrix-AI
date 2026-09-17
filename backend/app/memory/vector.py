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
            index = hash(token) % self.dim
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
    """In-process cosine store persisted to a JSON file."""

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
        self._docs[doc.doc_id] = doc
        self._rebuild()

    def add_many(self, docs: list[VectorDoc]) -> None:
        for doc in docs:
            self._docs[doc.doc_id] = doc
        self._rebuild()

    def has(self, doc_id: str) -> bool:
        return doc_id in self._docs

    def count(self) -> int:
        return len(self._docs)

    def search(self, query: str, k: int = 4, min_score: float = 0.0) -> list[SearchHit]:
        if self._matrix.shape[0] == 0:
            return []
        q = self.embedder.embed(query)
        if float(np.linalg.norm(q)) < 1e-9:
            return []
        # Rows and query are both L2-normalised, so the dot product *is* cosine.
        scores = self._matrix @ q
        order = np.argsort(-scores)[: max(k, 1)]
        return [
            SearchHit(doc=self._docs[self._ids[i]], score=float(scores[i]))
            for i in order
            if scores[i] >= min_score
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
