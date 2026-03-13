#!/usr/bin/env python3

"""Semantic search helpers for ai-lens."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    from .index_store import flatten_symbols, load_json, write_json
except ImportError:
    from index_store import flatten_symbols, load_json, write_json


SEMANTIC_SCHEMA_VERSION = 1
DEFAULT_EMBEDDING_MODEL = "all-MiniLM-L6-v2"
DEFAULT_TFIDF_MIN_SCORE = 0.05
DEFAULT_LEXICAL_MIN_SCORE = 0.1


@dataclass(frozen=True)
class IndexContext:
    project_root: Path
    index_dir: Path
    manifest_path: Path
    semantic_dir: Path
    corpus_path: Path
    tfidf_path: Path
    embeddings_path: Path
    meta_path: Path


def load_manifest(index_or_project_path: str | Path) -> tuple[dict[str, Any], IndexContext]:
    context = resolve_index_context(index_or_project_path)
    manifest = json.loads(context.manifest_path.read_text(encoding="utf-8"))
    return manifest, context


def resolve_index_context(index_or_project_path: str | Path) -> IndexContext:
    path = Path(index_or_project_path).resolve()
    if path.is_dir():
        manifest_path = path / ".ai-lens" / "manifest.json"
        if path.name == ".ai-lens" and (path / "manifest.json").exists():
            manifest_path = path / "manifest.json"
            project_root = path.parent
        else:
            project_root = path
        if not manifest_path.exists():
            snapshot_path = path / ".ai-lens.json"
            if snapshot_path.exists():
                manifest_path = snapshot_path
            else:
                raise FileNotFoundError(f"No ai-lens manifest found under {path}")
    elif path.is_file():
        manifest_path = path
        if path.name == "manifest.json" and path.parent.name == ".ai-lens":
            project_root = path.parent.parent
        else:
            project_root = path.parent
    else:
        raise FileNotFoundError(f"Could not resolve ai-lens index from {path}")

    index_dir = project_root / ".ai-lens"
    semantic_dir = index_dir / "semantic"
    return IndexContext(
        project_root=project_root,
        index_dir=index_dir,
        manifest_path=manifest_path,
        semantic_dir=semantic_dir,
        corpus_path=semantic_dir / "corpus.json",
        tfidf_path=semantic_dir / "tfidf.joblib",
        embeddings_path=semantic_dir / "embeddings.npz",
        meta_path=semantic_dir / "semantic_meta.json",
    )


def dependency_status() -> dict[str, Any]:
    return {
        "tfidf": _tfidf_dependency_status(),
        "embedding": _embedding_dependency_status(),
    }


def build_symbol_corpus(manifest_or_path: dict[str, Any] | str | Path) -> list[dict[str, Any]]:
    if isinstance(manifest_or_path, dict):
        manifest = manifest_or_path
    else:
        manifest, _ = load_manifest(manifest_or_path)

    corpus: list[dict[str, Any]] = []
    for file_entry in manifest.get("files", []):
        flattened = flatten_symbols(file_entry)
        if not flattened:
            continue
        for symbol in flattened:
            document = _symbol_document(file_entry, symbol)
            if not document["text"]:
                continue
            corpus.append(document)
    return corpus


def build_semantic_cache(
    index_or_project_path: str | Path,
    *,
    rebuild: bool = False,
    build_embeddings: bool = True,
    embedding_model: str = DEFAULT_EMBEDDING_MODEL,
) -> dict[str, Any]:
    manifest, context = load_manifest(index_or_project_path)
    manifest_fingerprint = _manifest_fingerprint(manifest, context.manifest_path)
    meta = load_json(context.meta_path) or {}

    cache_ready = (
        not rebuild
        and meta.get("schema_version") == SEMANTIC_SCHEMA_VERSION
        and meta.get("manifest_fingerprint") == manifest_fingerprint
        and context.corpus_path.exists()
    )
    if cache_ready:
        if meta.get("engines", {}).get("tfidf", {}).get("ready") and context.tfidf_path.exists():
            if not build_embeddings or meta.get("engines", {}).get("embedding", {}).get("ready") == context.embeddings_path.exists():
                return meta

    corpus = build_symbol_corpus(manifest)
    context.semantic_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        context.corpus_path,
        {
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "manifest_fingerprint": manifest_fingerprint,
            "generated_at": _utc_now(),
            "entries": corpus,
        },
    )

    tfidf_meta = _build_tfidf_cache(context, corpus)
    embedding_meta = _build_embedding_cache(context, corpus, model_name=embedding_model) if build_embeddings else {
        "ready": False,
        "available": False,
        "reason": "embedding build disabled",
        "engine": "embedding",
    }
    meta = {
        "schema_version": SEMANTIC_SCHEMA_VERSION,
        "manifest_fingerprint": manifest_fingerprint,
        "generated_at": _utc_now(),
        "project_root": str(context.project_root),
        "manifest_path": str(context.manifest_path),
        "corpus_size": len(corpus),
        "engines": {
            "tfidf": tfidf_meta,
            "embedding": embedding_meta,
        },
        "dependencies": dependency_status(),
    }
    write_json(context.meta_path, meta)
    return meta


def query_semantic(
    index_or_project_path: str | Path,
    query: str,
    *,
    top_k: int = 10,
    engine: str = "auto",
    min_score: float | None = None,
    rebuild: bool = False,
) -> dict[str, Any]:
    manifest, context = load_manifest(index_or_project_path)
    meta = build_semantic_cache(context.manifest_path, rebuild=rebuild)
    corpus_entries = (load_json(context.corpus_path) or {}).get("entries", [])
    if not corpus_entries:
        return {
            "mode": "semantic",
            "query": query,
            "engine": "none",
            "results": [],
            "meta": {
                "fallback_used": True,
                "reason": "empty corpus",
                "dependencies": dependency_status(),
            },
        }

    requested_engine = engine.lower()
    chosen_engine = _choose_engine(meta, requested_engine)
    if chosen_engine == "embedding":
        results = _query_embedding(context, query, corpus_entries, top_k, min_score or DEFAULT_TFIDF_MIN_SCORE)
        if results:
            return _semantic_payload(query, "embedding", results, meta, fallback_used=False)
        chosen_engine = "tfidf"

    if chosen_engine == "tfidf":
        results = _query_tfidf(context, query, corpus_entries, top_k, min_score or DEFAULT_TFIDF_MIN_SCORE)
        if results:
            return _semantic_payload(query, "tfidf", results, meta, fallback_used=False)
        chosen_engine = "lexical"

    results = _query_lexical(corpus_entries, query, top_k, min_score or DEFAULT_LEXICAL_MIN_SCORE)
    reason = "lexical fallback"
    if requested_engine not in {"auto", chosen_engine}:
        reason = f"requested engine {requested_engine} unavailable"
    return _semantic_payload(query, "lexical", results, meta, fallback_used=True, reason=reason)


def semantic_status(index_or_project_path: str | Path) -> dict[str, Any]:
    _, context = load_manifest(index_or_project_path)
    meta = load_json(context.meta_path)
    if meta is None:
        return {
            "schema_version": SEMANTIC_SCHEMA_VERSION,
            "ready": False,
            "dependencies": dependency_status(),
        }
    status = dict(meta)
    status["dependencies"] = dependency_status()
    return status


def _build_tfidf_cache(context: IndexContext, corpus: list[dict[str, Any]]) -> dict[str, Any]:
    dependency = _tfidf_dependency_status()
    if not dependency["available"]:
        return {
            "ready": False,
            "available": False,
            "reason": dependency["reason"],
            "engine": "tfidf",
        }

    try:
        from joblib import dump
        from sklearn.feature_extraction.text import TfidfVectorizer
    except ImportError as exc:  # pragma: no cover - environment dependent
        return {
            "ready": False,
            "available": False,
            "reason": str(exc),
            "engine": "tfidf",
        }

    documents = [entry["text"] for entry in corpus]
    ids = [entry["id"] for entry in corpus]
    vectorizer = TfidfVectorizer(ngram_range=(1, 2), stop_words="english", max_features=10_000)
    matrix = vectorizer.fit_transform(documents) if documents else None
    dump({"vectorizer": vectorizer, "matrix": matrix, "ids": ids}, context.tfidf_path)
    return {
        "ready": True,
        "available": True,
        "engine": "tfidf",
        "corpus_size": len(corpus),
        "path": str(context.tfidf_path),
    }


def _build_embedding_cache(
    context: IndexContext,
    corpus: list[dict[str, Any]],
    *,
    model_name: str,
) -> dict[str, Any]:
    dependency = _embedding_dependency_status()
    if not dependency["available"]:
        return {
            "ready": False,
            "available": False,
            "reason": dependency["reason"],
            "engine": "embedding",
            "model": model_name,
        }

    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - environment dependent
        return {
            "ready": False,
            "available": False,
            "reason": str(exc),
            "engine": "embedding",
            "model": model_name,
        }

    model = SentenceTransformer(model_name)
    embeddings = model.encode(
        [entry["text"] for entry in corpus],
        show_progress_bar=False,
        normalize_embeddings=True,
    )
    np.savez_compressed(
        context.embeddings_path,
        embeddings=embeddings,
        ids=np.array([entry["id"] for entry in corpus]),
        model=np.array([model_name]),
    )
    return {
        "ready": True,
        "available": True,
        "engine": "embedding",
        "model": model_name,
        "corpus_size": len(corpus),
        "path": str(context.embeddings_path),
    }


def _query_tfidf(
    context: IndexContext,
    query: str,
    corpus_entries: list[dict[str, Any]],
    top_k: int,
    min_score: float,
) -> list[dict[str, Any]]:
    if not context.tfidf_path.exists():
        return []
    try:
        from joblib import load
        from sklearn.metrics.pairwise import cosine_similarity
    except ImportError:
        return []

    payload = load(context.tfidf_path)
    vectorizer = payload["vectorizer"]
    matrix = payload["matrix"]
    ids = payload["ids"]
    if matrix is None:
        return []

    query_vector = vectorizer.transform([query])
    scores = cosine_similarity(query_vector, matrix).flatten()
    entry_by_id = {entry["id"]: entry for entry in corpus_entries}
    ranked = sorted(
        ((ids[index], float(score)) for index, score in enumerate(scores) if float(score) >= min_score),
        key=lambda item: (-item[1], item[0]),
    )[:top_k]
    return [_materialize_result(entry_by_id[item_id], score) for item_id, score in ranked if item_id in entry_by_id]


def _query_embedding(
    context: IndexContext,
    query: str,
    corpus_entries: list[dict[str, Any]],
    top_k: int,
    min_score: float,
) -> list[dict[str, Any]]:
    if not context.embeddings_path.exists():
        return []
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer
    except ImportError:
        return []

    payload = np.load(context.embeddings_path, allow_pickle=True)
    embeddings = payload["embeddings"]
    ids = payload["ids"].tolist()
    model_name = payload["model"].tolist()[0]
    model = SentenceTransformer(model_name)
    query_vector = model.encode([query], show_progress_bar=False, normalize_embeddings=True)[0]
    scores = embeddings @ query_vector
    entry_by_id = {entry["id"]: entry for entry in corpus_entries}
    ranked = sorted(
        ((ids[index], float(score)) for index, score in enumerate(scores) if float(score) >= min_score),
        key=lambda item: (-item[1], item[0]),
    )[:top_k]
    return [_materialize_result(entry_by_id[item_id], score) for item_id, score in ranked if item_id in entry_by_id]


def _query_lexical(
    corpus_entries: list[dict[str, Any]],
    query: str,
    top_k: int,
    min_score: float,
) -> list[dict[str, Any]]:
    keywords = [part.lower() for part in re.split(r"[\s/_-]+", query) if part.strip()]
    if not keywords:
        return []

    ranked: list[tuple[dict[str, Any], float]] = []
    for entry in corpus_entries:
        name = entry["symbol_name"].lower()
        haystack = entry["text"].lower()
        score = 0.0
        for keyword in keywords:
            if name == keyword:
                score += 3.0
            elif keyword in name:
                score += 2.0
            elif keyword in haystack:
                score += 1.0
        if not keywords:
            continue
        score = score / (2.5 * len(keywords))
        if score >= min_score:
            ranked.append((entry, min(score, 1.0)))
    ranked.sort(key=lambda item: (-item[1], item[0]["id"]))
    return [_materialize_result(entry, score) for entry, score in ranked[:top_k]]


def _semantic_payload(
    query: str,
    engine: str,
    results: list[dict[str, Any]],
    meta: dict[str, Any],
    *,
    fallback_used: bool,
    reason: str | None = None,
) -> dict[str, Any]:
    payload = {
        "mode": "semantic",
        "query": query,
        "engine": engine,
        "results": results,
        "meta": {
            "fallback_used": fallback_used,
            "reason": reason,
            "corpus_size": meta.get("corpus_size", 0),
            "engines": meta.get("engines", {}),
            "dependencies": meta.get("dependencies", dependency_status()),
        },
    }
    return payload





def _symbol_document(file_entry: dict[str, Any], symbol: dict[str, Any]) -> dict[str, Any]:
    path = file_entry["path"]
    symbol_name = symbol["name"]
    parent = symbol.get("parent")
    doc_text = symbol.get("doc", "")
    imports = " ".join(file_entry.get("imports", []))
    exports = " ".join(file_entry.get("exports", []))
    fields = [
        symbol_name,
        symbol.get("signature", ""),
        doc_text,
        path,
        imports,
        exports,
        parent or "",
    ]
    text = " ".join(field for field in fields if field).strip()
    return {
        "id": f"{path}::{parent + '.' if parent else ''}{symbol_name}",
        "path": path,
        "symbol_name": symbol_name,
        "symbol_type": symbol.get("type", ""),
        "signature": symbol.get("signature", ""),
        "doc": doc_text,
        "line_start": symbol.get("line_start"),
        "line_end": symbol.get("line_end"),
        "parent": parent,
        "imports": file_entry.get("imports", []),
        "exports": file_entry.get("exports", []),
        "rank": file_entry.get("rank", 0.0),
        "text": _normalize_text(text),
    }


def _materialize_result(entry: dict[str, Any], score: float) -> dict[str, Any]:
    boosted = score * (0.5 + min(max(float(entry.get("rank", 0.0)), 0.0), 20.0) / 20.0)
    return {
        "path": entry["path"],
        "score": round(float(score), 4),
        "boosted_score": round(float(boosted), 4),
        "rank": entry.get("rank", 0.0),
        "symbol": {
            "name": entry["symbol_name"],
            "type": entry["symbol_type"],
            "signature": entry["signature"],
            "line_start": entry["line_start"],
            "line_end": entry["line_end"],
            "parent": entry.get("parent"),
            "doc": entry.get("doc") or None,
        },
    }


def _manifest_fingerprint(manifest: dict[str, Any], manifest_path: Path) -> str:
    payload = {
        "generated_at_ns": manifest.get("generated_at_ns"),
        "project_root": manifest.get("project", {}).get("root"),
        "file_count": len(manifest.get("files", [])),
        "stats": manifest.get("stats", {}),
    }
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True).encode("utf-8"))
    if manifest_path.exists():
        digest.update(manifest_path.read_bytes())
    return digest.hexdigest()





def _choose_engine(meta: dict[str, Any], requested_engine: str) -> str:
    engines = meta.get("engines", {})
    if requested_engine in {"embedding", "tfidf", "lexical"}:
        return requested_engine
    if engines.get("tfidf", {}).get("ready"):
        return "tfidf"
    if engines.get("embedding", {}).get("ready"):
        return "embedding"
    return "lexical"


def _tfidf_dependency_status() -> dict[str, Any]:
    try:
        import joblib  # noqa: F401
        import sklearn  # noqa: F401
    except ImportError as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "reason": None}


def _embedding_dependency_status() -> dict[str, Any]:
    try:
        import numpy  # noqa: F401
        import sentence_transformers  # noqa: F401
    except ImportError as exc:
        return {"available": False, "reason": str(exc)}
    return {"available": True, "reason": None}


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
