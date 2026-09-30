"""PDF to per-page text. Done locally so any model (including ones that cannot read PDFs) sees the file."""
from __future__ import annotations

import io
import re

import pdfplumber

MIN_TEXT_CHARS = 25  # below this a page is probably a scan or a photo


def extract_pages(pdf_bytes: bytes) -> list[dict]:
    pages = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for i, p in enumerate(pdf.pages, start=1):
            text = (p.extract_text() or "").strip()
            pages.append({"page_no": i, "text": text, "scanned": len(text) < MIN_TEXT_CHARS})
    return pages


def file_for_prompt(pages: list[dict], limit_chars: int = 80_000) -> str:
    """Pages joined with markers so the model can cite page numbers."""
    parts = []
    for p in pages:
        body = p["text"] if p["text"] else "(no text on this page: likely a photo or a scan)"
        parts.append(f"[page {p['page_no']}]\n{body}")
    s = "\n\n".join(parts)
    return s if len(s) <= limit_chars else s[:limit_chars] + "\n\n[file truncated]"


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def quote_page(pages: list[dict], quote: str) -> int | None:
    """Page where the quote appears word for word (whitespace and case ignored), else None."""
    q = _norm(quote)
    if len(q) < 6:
        return None
    for p in pages:
        if q in _norm(p["text"]):
            return p["page_no"]
    return None


def search(pages: list[dict], query: str, limit: int = 12) -> list[dict]:
    """Lines containing the query (case-insensitive), with page numbers."""
    q = query.lower().strip()
    hits = []
    if not q:
        return hits
    for p in pages:
        for line in p["text"].splitlines():
            if q in line.lower():
                hits.append({"page": p["page_no"], "line": line.strip()[:300]})
                if len(hits) >= limit:
                    return hits
    return hits
