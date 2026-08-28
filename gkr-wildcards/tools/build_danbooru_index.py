#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///

"""Build a reusable lexical and optional semantic index for a Danbooru tag CSV."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import struct
import sys
import time
from pathlib import Path

from danbooru_index import EmbeddingClient, build_index, file_sha256


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("csv", type=Path, help="Danbooru-compatible tag CSV")
    parser.add_argument("--output", type=Path, help="SQLite destination (defaults beside CSV)")
    parser.add_argument("--rebuild", action="store_true", help="Rebuild the deterministic index even when compatible")
    parser.add_argument(
        "--content-profile", choices=("general", "sensitive", "unrestricted"), default="general",
        help="Filter classified tags before indexing (default: general)",
    )
    parser.add_argument("--embeddings", action="store_true", help="Build or resume semantic embeddings")
    parser.add_argument("--embedding-model")
    parser.add_argument("--embedding-base-url", default="http://localhost:11434/v1")
    parser.add_argument("--embedding-api-key-env", default="OLLAMA_API_KEY")
    parser.add_argument("--embedding-batch-size", type=int, default=512)
    parser.add_argument("--embedding-dimensions", type=int)
    parser.add_argument("--embedding-document-prefix", default="")
    parser.add_argument("--embedding-query-prefix", default="")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--no-resume", action="store_true", help="Discard existing vectors before embedding")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args()


def log(args: argparse.Namespace, message: str) -> None:
    if args.verbose:
        print(f"[danbooru-index] {message}", file=sys.stderr)


def metadata(connection: sqlite3.Connection) -> dict[str, object]:
    return {key: json.loads(value) for key, value in connection.execute("SELECT key,value FROM metadata")}


def set_metadata(connection: sqlite3.Connection, values: dict[str, object]) -> None:
    connection.executemany(
        "INSERT INTO metadata(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        [(key, json.dumps(value)) for key, value in values.items()],
    )


def main() -> int:
    args = parse_args()
    try:
        csv_path = args.csv.expanduser().resolve()
        output = (args.output or csv_path.with_suffix(".index.sqlite")).expanduser().resolve()
        if args.embedding_batch_size < 1 or args.timeout < 1:
            raise ValueError("batch size and timeout must be positive")
        rebuild = args.rebuild or not output.exists()
        if not rebuild:
            connection = sqlite3.connect(output)
            try:
                current = metadata(connection)
            finally:
                connection.close()
            rebuild = (
                current.get("source_csv_sha256") != file_sha256(csv_path)
                or current.get("content_profile") != args.content_profile
            )
        if rebuild:
            info = build_index(csv_path, output, args.content_profile)
            log(args, f"built lexical index with {info['tag_count']} tags")
        else:
            log(args, "existing lexical index matches CSV")

        if not args.embeddings:
            print(output)
            return 0
        if not args.embedding_model:
            raise ValueError("--embedding-model is required with --embeddings")
        api_key = os.getenv(args.embedding_api_key_env, "")
        client = EmbeddingClient(
            args.embedding_base_url, args.embedding_model, api_key,
            args.timeout, args.embedding_dimensions,
        )
        connection = sqlite3.connect(output)
        try:
            if args.no_resume:
                connection.execute("UPDATE tags SET embedding=NULL, embedding_dimensions=NULL")
            current = metadata(connection)
            prior_model = current.get("embedding_model")
            prior_url = current.get("embedding_base_url")
            prior_prefix = current.get("embedding_document_prefix", "")
            prior_query_prefix = current.get("embedding_query_prefix", "")
            incompatible = any((
                prior_model not in (None, args.embedding_model),
                prior_url not in (None, args.embedding_base_url.rstrip("/")),
                prior_prefix != args.embedding_document_prefix and prior_model is not None,
                prior_query_prefix != args.embedding_query_prefix and prior_model is not None,
            ))
            if incompatible and connection.execute("SELECT COUNT(*) FROM tags WHERE embedding IS NOT NULL").fetchone()[0]:
                raise ValueError("existing partial embeddings use different model, URL, or prefix; use --no-resume")
            set_metadata(connection, {
                "embedding_status": "building",
                "embedding_model": args.embedding_model,
                "embedding_base_url": args.embedding_base_url.rstrip("/"),
                "embedding_document_prefix": args.embedding_document_prefix,
                "embedding_query_prefix": args.embedding_query_prefix,
                "embedding_requested_dimensions": args.embedding_dimensions,
                "embedding_started": time.time(),
            })
            connection.commit()
            rows = connection.execute(
                "SELECT row_id,name FROM tags WHERE embedding IS NULL ORDER BY row_id"
            ).fetchall()
            total = connection.execute("SELECT COUNT(*) FROM tags").fetchone()[0]
            completed = total - len(rows)
            dimensions: int | None = None
            existing_dimension = connection.execute(
                "SELECT embedding_dimensions FROM tags WHERE embedding IS NOT NULL LIMIT 1"
            ).fetchone()
            if existing_dimension:
                dimensions = int(existing_dimension[0])
            for offset in range(0, len(rows), args.embedding_batch_size):
                batch = rows[offset:offset + args.embedding_batch_size]
                inputs = [args.embedding_document_prefix + str(row[1]).replace("_", " ") for row in batch]
                vectors = client.embed(inputs)
                if dimensions is None:
                    dimensions = len(vectors[0])
                if any(len(vector) != dimensions for vector in vectors):
                    raise RuntimeError("embedding dimensions changed during the build")
                for row, vector in zip(batch, vectors):
                    connection.execute(
                        "UPDATE tags SET embedding=?,embedding_dimensions=? WHERE row_id=?",
                        (struct.pack(f"<{dimensions}f", *vector), dimensions, int(row[0])),
                    )
                completed += len(batch)
                set_metadata(connection, {"embedding_completed": completed})
                connection.commit()
                log(args, f"embedded {completed}/{total} tags")
            set_metadata(connection, {
                "embedding_status": "complete",
                "embedding_dimensions": dimensions or 0,
                "embedding_completed": total,
                "embedding_finished": time.time(),
            })
            connection.commit()
        finally:
            connection.close()
        print(output)
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
        print(f"build_danbooru_index: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
