"""Reusable deterministic and semantic retrieval for Danbooru tag CSV files."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import re
import sqlite3
import struct
import unicodedata
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

try:
    import numpy as np
except ImportError:  # The deterministic index builder does not require NumPy.
    np = None


SCHEMA_VERSION = 1
TOKEN_RE = re.compile(r"[a-z0-9]+")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    value = re.sub(r"[_\-–—]+", " ", value)
    value = re.sub(r"[^a-z0-9+.' ]+", " ", value)
    return " ".join(value.split())


def token_variants(value: str) -> set[str]:
    normalized = normalize_text(value)
    variants = {normalized}
    words = normalized.split()
    if words:
        final = words[-1]
        if final.endswith("ies") and len(final) > 3:
            variants.add(" ".join(words[:-1] + [final[:-3] + "y"]))
        elif final.endswith("s") and not final.endswith("ss") and len(final) > 2:
            variants.add(" ".join(words[:-1] + [final[:-1]]))
        else:
            variants.add(" ".join(words[:-1] + [final + "s"]))
    return {value for value in variants if value}


def trigrams(value: str) -> set[str]:
    compact = f"  {normalize_text(value)}  "
    return {compact[index:index + 3] for index in range(max(0, len(compact) - 2))}


@dataclass(frozen=True)
class TagRecord:
    row_id: int
    name: str
    post_count: int
    aliases: tuple[str, ...]
    content_class: str = "unclassified"


@dataclass(frozen=True)
class SearchResult:
    tag: str
    post_count: int
    score: float
    match: str


class EmbeddingClient:
    """Small provider-neutral client for OpenAI-compatible /v1/embeddings."""

    def __init__(
        self, base_url: str, model: str, api_key: str = "", timeout: int = 120,
        dimensions: int | None = None,
    ):
        root = base_url.rstrip("/")
        self.endpoint = root if root.endswith("/embeddings") else f"{root}/embeddings"
        self.model = model
        self.api_key = api_key
        self.timeout = timeout
        self.dimensions = dimensions

    def embed(self, inputs: Sequence[str]) -> list[list[float]]:
        if not inputs:
            return []
        payload: dict[str, Any] = {"model": self.model, "input": list(inputs), "encoding_format": "float"}
        if self.dimensions is not None:
            payload["dimensions"] = self.dimensions
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"embedding request failed: HTTP {exc.code}: {detail[:2000]}") from exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"embedding request failed: {type(exc).__name__}: {exc}") from exc
        data = body.get("data")
        if not isinstance(data, list):
            raise RuntimeError("embedding response does not contain a data array")
        ordered: list[list[float] | None] = [None] * len(inputs)
        for fallback_index, item in enumerate(data):
            if not isinstance(item, dict) or not isinstance(item.get("embedding"), list):
                raise RuntimeError("embedding response contains an invalid data item")
            try:
                index = int(item.get("index", fallback_index))
                vector = [float(value) for value in item["embedding"]]
            except (TypeError, ValueError) as exc:
                raise RuntimeError("embedding response contains invalid vector values") from exc
            if index < 0 or index >= len(ordered) or ordered[index] is not None:
                raise RuntimeError("embedding response contains invalid or duplicate indices")
            ordered[index] = vector
        if any(vector is None for vector in ordered):
            raise RuntimeError(f"embedding response returned {len(data)} vectors for {len(inputs)} inputs")
        result = [vector for vector in ordered if vector is not None]
        dimensions = len(result[0])
        if dimensions < 1 or any(len(vector) != dimensions for vector in result):
            raise RuntimeError("embedding response dimensions are inconsistent")
        if not all(math.isfinite(value) for vector in result for value in vector):
            raise RuntimeError("embedding response contains non-finite values")
        return result


def read_csv_tags(path: Path) -> list[TagRecord]:
    records: list[TagRecord] = []
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "name" not in reader.fieldnames:
            raise ValueError("tag CSV must contain a name column")
        for row_id, row in enumerate(reader):
            name = str(row.get("name", "")).strip()
            if not name:
                continue
            try:
                post_count = int(row.get("post_count") or 0)
            except ValueError:
                post_count = 0
            raw_alias = str(row.get("alias", "") or "")
            aliases = tuple(dict.fromkeys(
                part.strip() for part in re.split(r"[|;]", raw_alias) if part.strip()
            ))
            content_class = str(row.get("content_class", "unclassified") or "unclassified").strip().lower()
            records.append(TagRecord(row_id, name, post_count, aliases, content_class))
    return records


def build_index(csv_path: Path, index_path: Path, content_profile: str = "unrestricted") -> dict[str, Any]:
    records = read_csv_tags(csv_path)
    if not records:
        raise ValueError("tag CSV contains no tags")
    source_count = len(records)
    allowed_classes = {
        "general": {"general"},
        "sensitive": {"general", "sensitive"},
        "unrestricted": {"general", "sensitive", "explicit", "ambiguous", "unclassified"},
    }
    if content_profile not in allowed_classes:
        raise ValueError(f"unknown content profile: {content_profile}")
    if content_profile != "unrestricted" and any(record.content_class == "unclassified" for record in records):
        raise ValueError(
            f"content profile '{content_profile}' requires a CSV with content_class values; "
            "classify the CSV first or explicitly select unrestricted"
        )
    records = [record for record in records if record.content_class in allowed_classes[content_profile]]
    if not records:
        raise ValueError(f"content profile '{content_profile}' excludes every tag")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = index_path.with_name(index_path.name + ".tmp")
    temporary.unlink(missing_ok=True)
    connection = sqlite3.connect(temporary)
    try:
        connection.executescript("""
            PRAGMA journal_mode = OFF;
            PRAGMA synchronous = OFF;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE tags (
                row_id INTEGER PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                normalized TEXT NOT NULL,
                post_count INTEGER NOT NULL,
                aliases_json TEXT NOT NULL,
                variants_json TEXT NOT NULL,
                trigrams_json TEXT NOT NULL,
                content_class TEXT NOT NULL,
                embedding BLOB,
                embedding_dimensions INTEGER
            );
            CREATE TABLE lookup (
                normalized TEXT NOT NULL,
                tag_row_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                UNIQUE(normalized, tag_row_id, kind)
            );
            CREATE INDEX lookup_normalized ON lookup(normalized);
            CREATE VIRTUAL TABLE tags_fts USING fts5(name, normalized, aliases, tokenize='unicode61');
        """)
        for record in records:
            normalized = normalize_text(record.name)
            variants = set(token_variants(record.name))
            aliases = set(record.aliases)
            for alias in aliases:
                variants.update(token_variants(alias))
            connection.execute(
                "INSERT INTO tags VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL)",
                (
                    record.row_id, record.name, normalized, record.post_count,
                    json.dumps(sorted(aliases)), json.dumps(sorted(variants)),
                    json.dumps(sorted(trigrams(record.name))), record.content_class,
                ),
            )
            connection.execute(
                "INSERT INTO tags_fts(rowid, name, normalized, aliases) VALUES (?, ?, ?, ?)",
                (record.row_id + 1, record.name, normalized, " ".join(normalize_text(value) for value in aliases)),
            )
            connection.execute("INSERT OR IGNORE INTO lookup VALUES (?, ?, 'canonical')", (normalized, record.row_id))
            for alias in aliases:
                connection.execute("INSERT OR IGNORE INTO lookup VALUES (?, ?, 'alias')", (normalize_text(alias), record.row_id))
            for variant in variants - {normalized}:
                connection.execute("INSERT OR IGNORE INTO lookup VALUES (?, ?, 'variant')", (variant, record.row_id))
        metadata = {
            "schema_version": SCHEMA_VERSION,
            "source_csv": str(csv_path.resolve()),
            "source_csv_sha256": file_sha256(csv_path),
            "tag_count": len(records),
            "source_tag_count": source_count,
            "excluded_tag_count": source_count - len(records),
            "content_profile": content_profile,
            "embedding_status": "absent",
        }
        connection.executemany(
            "INSERT INTO metadata VALUES (?, ?)",
            [(key, json.dumps(value)) for key, value in metadata.items()],
        )
        connection.commit()
    finally:
        connection.close()
    temporary.replace(index_path)
    return metadata


class DanbooruIndex:
    def __init__(self, path: Path):
        self.path = path.resolve()
        self.connection = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True)
        self.connection.row_factory = sqlite3.Row
        self.metadata = {
            row["key"]: json.loads(row["value"])
            for row in self.connection.execute("SELECT key, value FROM metadata")
        }
        if self.metadata.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"unsupported tag-index schema {self.metadata.get('schema_version')}; expected {SCHEMA_VERSION}"
            )
        self._embedding_rows: list[tuple[str, int, bytes]] | None = None
        self._embedding_matrix: Any = None

    def close(self) -> None:
        self.connection.close()

    def compatible_with_csv(self, csv_path: Path) -> bool:
        return self.metadata.get("source_csv_sha256") == file_sha256(csv_path)

    def contains_tag(self, name: str) -> bool:
        return self.connection.execute("SELECT 1 FROM tags WHERE name=?", (name,)).fetchone() is not None

    def canonical_names(self) -> set[str]:
        return {str(row[0]) for row in self.connection.execute("SELECT name FROM tags")}

    @property
    def has_embeddings(self) -> bool:
        return self.metadata.get("embedding_status") == "complete"

    def lexical_search(self, query: str, limit: int = 12) -> list[SearchResult]:
        normalized = normalize_text(query)
        if not normalized:
            return []
        results: dict[str, SearchResult] = {}
        priority = {"canonical": 1.0, "alias": 0.98, "variant": 0.94}
        for row in self.connection.execute(
            "SELECT tags.name, tags.post_count, lookup.kind FROM lookup JOIN tags ON tags.row_id=lookup.tag_row_id WHERE lookup.normalized=?",
            (normalized,),
        ):
            candidate = SearchResult(row["name"], row["post_count"], priority[row["kind"]], row["kind"])
            current = results.get(row["name"])
            if current is None or candidate.score > current.score:
                results[row["name"]] = candidate

        tokens = TOKEN_RE.findall(normalized)
        if tokens:
            expression = " OR ".join(f'"{token}"*' for token in tokens)
            try:
                rows = self.connection.execute(
                    "SELECT tags.name, tags.post_count, bm25(tags_fts) AS rank "
                    "FROM tags_fts JOIN tags ON tags.row_id=tags_fts.rowid-1 "
                    "WHERE tags_fts MATCH ? ORDER BY rank LIMIT ?",
                    (expression, max(limit * 4, 30)),
                )
                for row in rows:
                    overlap = len(set(tokens) & set(normalize_text(row["name"]).split())) / max(1, len(set(tokens)))
                    score = 0.55 + 0.30 * overlap + 0.02 * math.log10(max(1, row["post_count"])) / 7
                    current = results.get(row["name"])
                    candidate = SearchResult(row["name"], row["post_count"], min(score, 0.9), "lexical")
                    if current is None or candidate.score > current.score:
                        results[row["name"]] = candidate
            except sqlite3.OperationalError:
                pass

        query_trigrams = trigrams(normalized)
        candidates = self.connection.execute(
            "SELECT name, post_count, trigrams_json FROM tags ORDER BY post_count DESC LIMIT 3000"
        )
        for row in candidates:
            tag_trigrams = set(json.loads(row["trigrams_json"]))
            union = query_trigrams | tag_trigrams
            similarity = len(query_trigrams & tag_trigrams) / len(union) if union else 0.0
            if similarity < 0.28:
                continue
            score = 0.35 + 0.45 * similarity + 0.02 * math.log10(max(1, row["post_count"])) / 7
            current = results.get(row["name"])
            candidate = SearchResult(row["name"], row["post_count"], min(score, 0.89), "trigram")
            if current is None or candidate.score > current.score:
                results[row["name"]] = candidate
        return sorted(results.values(), key=lambda item: (-item.score, -item.post_count, item.tag))[:limit]

    def semantic_search(self, query_vector: Sequence[float], limit: int = 12) -> list[SearchResult]:
        if not self.has_embeddings:
            return []
        dimensions = int(self.metadata.get("embedding_dimensions", 0))
        if len(query_vector) != dimensions:
            raise ValueError(f"query embedding has {len(query_vector)} dimensions; index requires {dimensions}")
        if self._embedding_rows is None:
            self._embedding_rows = [
                (str(row["name"]), int(row["post_count"]), bytes(row["embedding"]))
                for row in self.connection.execute(
                    "SELECT name, post_count, embedding FROM tags WHERE embedding IS NOT NULL ORDER BY row_id"
                )
            ]
        if np is not None:
            if self._embedding_matrix is None:
                matrix = np.vstack([
                    np.frombuffer(blob, dtype="<f4", count=dimensions) for _, _, blob in self._embedding_rows
                ])
                norms = np.linalg.norm(matrix, axis=1, keepdims=True)
                norms[norms == 0] = 1.0
                self._embedding_matrix = matrix / norms
            query = np.asarray(query_vector, dtype=np.float32)
            norm = float(np.linalg.norm(query)) or 1.0
            scores = self._embedding_matrix @ (query / norm)
            count = min(limit, len(scores))
            indexes = np.argpartition(-scores, count - 1)[:count] if count else []
            ranked = sorted(indexes, key=lambda index: (-float(scores[index]), -self._embedding_rows[index][1]))
            return [
                SearchResult(self._embedding_rows[index][0], self._embedding_rows[index][1], float(scores[index]), "semantic")
                for index in ranked
            ]
        query_norm = math.sqrt(sum(float(value) ** 2 for value in query_vector)) or 1.0
        scored: list[SearchResult] = []
        for name, post_count, blob in self._embedding_rows:
            vector = struct.unpack(f"<{dimensions}f", blob)
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            cosine = sum(float(a) * b for a, b in zip(query_vector, vector)) / (query_norm * norm)
            scored.append(SearchResult(name, post_count, cosine, "semantic"))
        return sorted(scored, key=lambda item: (-item.score, -item.post_count, item.tag))[:limit]

    def hybrid_search(
        self, query: str, query_vector: Sequence[float] | None = None, limit: int = 12,
    ) -> list[SearchResult]:
        lexical = self.lexical_search(query, max(limit * 2, 20))
        combined: dict[str, SearchResult] = {item.tag: item for item in lexical}
        if query_vector is not None:
            semantic = self.semantic_search(query_vector, max(limit * 2, 20))
            for rank, item in enumerate(semantic):
                # Mirror the lexical trigram path's minimum-similarity gate (line ~333):
                # without a floor, every semantic hit scores >=0.52 regardless of true
                # cosine similarity, so an unrelated nearest neighbor for a niche/coined
                # concept can still outrank or crowd out a genuinely relevant lexical match.
                if item.score < 0.35:
                    continue
                semantic_score = 0.52 + 0.38 * max(0.0, item.score) + 0.03 / (rank + 1)
                current = combined.get(item.tag)
                candidate = SearchResult(item.tag, item.post_count, semantic_score, "hybrid" if current else "semantic")
                if current:
                    candidate = SearchResult(
                        item.tag, item.post_count, min(0.99, max(current.score, semantic_score) + 0.05), "hybrid"
                    )
                if current is None or candidate.score > current.score:
                    combined[item.tag] = candidate
        return sorted(combined.values(), key=lambda item: (-item.score, -item.post_count, item.tag))[:limit]


def write_embeddings(
    index_path: Path, vectors: dict[int, Sequence[float]], metadata: dict[str, Any],
) -> None:
    if not vectors:
        raise ValueError("no embeddings supplied")
    dimensions = len(next(iter(vectors.values())))
    if dimensions < 1 or any(len(vector) != dimensions for vector in vectors.values()):
        raise ValueError("embedding dimensions are inconsistent")
    connection = sqlite3.connect(index_path)
    try:
        expected = connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
        if len(vectors) != expected:
            raise ValueError(f"received {len(vectors)} embeddings for {expected} tags")
        for row_id, vector in vectors.items():
            if not all(math.isfinite(float(value)) for value in vector):
                raise ValueError(f"embedding {row_id} contains a non-finite value")
            blob = struct.pack(f"<{dimensions}f", *(float(value) for value in vector))
            connection.execute(
                "UPDATE tags SET embedding=?, embedding_dimensions=? WHERE row_id=?",
                (blob, dimensions, row_id),
            )
        values = {
            "embedding_status": "complete", "embedding_dimensions": dimensions, **metadata,
        }
        for key, value in values.items():
            connection.execute(
                "INSERT INTO metadata(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, json.dumps(value)),
            )
        connection.commit()
    finally:
        connection.close()
