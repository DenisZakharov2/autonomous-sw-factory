"""RAG-индекс репозитория фабрики для контекста ролей.

Индексирует отслеживаемые файлы репозитория (.py, .md, .yaml, .toml) и
отдаёт релевантные чанки по текстовому запросу. BM25 над стандартной
библиотекой — без внешних зависимостей и без доступа к сети.

Индекс хранится вне репозитория (в ~/.cache/factory/index), чтобы не
засорять git и не пересекаться с runs/ и examples/.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .config import PACKAGE_ROOT

INDEX_DIR = Path.home() / ".cache" / "factory" / "index"
INDEX_VERSION = 1

EXCLUDED_DIRS = {
    ".git",
    "__pycache__",
    ".venv",
    "node_modules",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "runs",
    "examples",
    ".archive",
}
INDEXED_EXTENSIONS = {".py", ".md", ".yaml", ".yml", ".toml"}

CHUNK_CHARS = 1200
CHUNK_OVERLAP = 200
TOP_K_DEFAULT = 5


@dataclass
class Chunk:
    path: str
    start_line: int
    end_line: int
    text: str
    tokens: list[str] = field(default_factory=list)
    score: float = 0.0


def _tokenize(text: str) -> list[str]:
    """Разбивает текст на токены: буквы (в т.ч. кириллица), цифры, подчёркивание."""
    return re.findall(r"[a-zа-яё0-9_]+", text.lower())


def _tracked_files(root: Path) -> list[Path]:
    """Список tracked файлов через git ls-files (репозиторий локальный)."""
    try:
        out = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        return []
    files: list[Path] = []
    for line in out.stdout.splitlines():
        if not line.strip():
            continue
        p = root / line
        if not p.is_file():
            continue
        if p.suffix.lower() not in INDEXED_EXTENSIONS:
            continue
        parts = set(p.relative_to(root).parts)
        if parts & EXCLUDED_DIRS:
            continue
        files.append(p)
    return files


def _chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Режет текст на чанки по размеру с наложением, не разрывая строк."""
    lines = text.splitlines(keepends=True)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        line_len = len(line)
        if current_len + line_len > size and current:
            chunks.append("".join(current))
            keep: list[str] = []
            keep_len = 0
            for prev in reversed(current):
                if keep_len + len(prev) > overlap:
                    break
                keep.insert(0, prev)
                keep_len += len(prev)
            current = keep
            current_len = keep_len
        current.append(line)
        current_len += line_len
    if current:
        chunks.append("".join(current))
    return chunks


def build_index(root: Path | None = None, index_dir: Path | None = None) -> dict[str, Any]:
    """Строит BM25-индекс tracked-файлов репозитория."""
    root = root or PACKAGE_ROOT.parent
    index_dir = index_dir or INDEX_DIR
    index_dir.mkdir(parents=True, exist_ok=True)

    files = _tracked_files(root)
    chunks: list[Chunk] = []
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        rel = str(path.relative_to(root))
        pieces = _chunk_text(text)
        char_cursor = 0
        for piece in pieces:
            idx = text.find(piece, char_cursor)
            if idx < 0:
                idx = char_cursor
            line_no = text.count("\n", 0, idx) + 1
            end_line = line_no + piece.count("\n")
            chunk_text = piece
            tokens = _tokenize(chunk_text)
            chunks.append(
                Chunk(
                    path=rel,
                    start_line=line_no,
                    end_line=end_line,
                    text=chunk_text,
                    tokens=tokens,
                )
            )
            char_cursor = max(0, idx + len(piece) - CHUNK_OVERLAP)

    N = len(chunks)
    df: dict[str, int] = defaultdict(int)
    for ch in chunks:
        seen = set(ch.tokens)
        for t in seen:
            df[t] += 1
    avgdl = sum(len(ch.tokens) for ch in chunks) / N if N else 0.0
    doc_lens = [len(ch.tokens) for ch in chunks]

    payload: dict[str, Any] = {
        "version": INDEX_VERSION,
        "root": str(root),
        "chunks": [
            {
                "path": ch.path,
                "start_line": ch.start_line,
                "end_line": ch.end_line,
                "text": ch.text,
                "tokens": ch.tokens,
            }
            for ch in chunks
        ],
        "df": dict(df),
        "avgdl": avgdl,
        "doc_lens": doc_lens,
    }
    (index_dir / "index.json").write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )
    return {
        "files": len(files),
        "chunks": N,
        "index_dir": str(index_dir),
    }


def _load(index_dir: Path | None = None) -> dict[str, Any]:
    index_dir = index_dir or INDEX_DIR
    return json.loads((index_dir / "index.json").read_text(encoding="utf-8"))


def _bm25_scores(
    query_tokens: list[str],
    chunks: list[dict[str, Any]],
    df: dict[str, int],
    avgdl: float,
    doc_lens: list[int],
    k1: float = 1.5,
    b: float = 0.75,
) -> list[float]:
    N = len(chunks)
    scores = [0.0] * N
    for qt in query_tokens:
        nqi = df.get(qt, 0)
        if nqi == 0:
            continue
        idf = math.log((N - nqi + 0.5) / (nqi + 0.5) + 1.0)
        for i, ch in enumerate(chunks):
            tf = ch["tokens"].count(qt)
            if tf == 0:
                continue
            dl = doc_lens[i] if i < len(doc_lens) else len(ch["tokens"])
            denom = tf + k1 * (1 - b + b * dl / avgdl) if avgdl else tf
            scores[i] += idf * (tf * (k1 + 1)) / denom
    return scores


def retrieve(
    query: str,
    *,
    index_dir: Path | None = None,
    k: int = TOP_K_DEFAULT,
) -> list[dict[str, Any]]:
    """Возвращает до k релевантных чанков с путями, строками и скором."""
    data = _load(index_dir)
    if Path(data.get("root", "")).resolve() != PACKAGE_ROOT.parent.resolve():
        data = build_index(PACKAGE_ROOT.parent, index_dir=index_dir)
    chunks = data["chunks"]
    df = data.get("df", {})
    avgdl = data.get("avgdl", 0.0)
    doc_lens = data.get("doc_lens", [])
    query_tokens = _tokenize(query)
    if not query_tokens or not chunks:
        return []
    scores = _bm25_scores(query_tokens, chunks, df, avgdl, doc_lens)
    ranked = sorted(range(len(chunks)), key=lambda i: scores[i], reverse=True)
    results: list[dict[str, Any]] = []
    for i in ranked[:k]:
        if scores[i] <= 0:
            continue
        ch = chunks[i]
        results.append(
            {
                "path": ch["path"],
                "start_line": ch["start_line"],
                "end_line": ch["end_line"],
                "score": round(scores[i], 4),
                "text": ch["text"],
            }
        )
    return results
