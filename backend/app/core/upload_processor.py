import os
import httpx
import logging
import mimetypes
from pathlib import Path
from typing import List, Optional, Dict, Any
from fastapi import HTTPException

from ..models.session import SessionState
from .session_store import get_session, update_session
from .chroma_store import store_chunks, collection_name

logger = logging.getLogger(__name__)

CEREBRUM_API_URL = os.getenv("CEREBRUM_API_URL", "http://localhost:8000")
CEREBRUM_API_KEY = os.getenv("CEREBRUM_API_KEY")
STORAGE_PATH = os.getenv("STORAGE_PATH", "./storage")


def _headers() -> Dict[str, str]:
    h = {"Content-Type": "application/json"}
    if CEREBRUM_API_KEY:
        h["Authorization"] = f"Bearer {CEREBRUM_API_KEY}"
    return h


def _session_storage_dir(session_id: str) -> Path:
    path = Path(STORAGE_PATH) / "sessions" / session_id / "files"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_type(file_path: str) -> str:
    mime, _ = mimetypes.guess_type(file_path)
    ext = Path(file_path).suffix.lower()
    if mime:
        if mime.startswith("image/"):
            return "image"
        if mime == "application/pdf" or ext == ".pdf":
            return "pdf"
        if mime in (
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "application/msword",
        ) or ext in (".docx", ".doc"):
            return "docx"
    if ext in (".txt", ".md", ".json", ".csv"):
        return "text"
    return "unknown"


async def _execute_block(block: str, input_data: Any, params: Optional[Dict] = None) -> Dict[str, Any]:
    """Call a Cerebrum-Blocks block via /v1/execute."""
    url = f"{CEREBRUM_API_URL}/v1/execute"
    payload = {"block": block, "input": input_data}
    if params:
        payload["params"] = params
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(url, json=payload, headers=_headers())
        resp.raise_for_status()
        return resp.json()


async def parse_document(file_path: str) -> str:
    """Extract text from a document using Cerebrum-Blocks blocks with local fallbacks."""
    doc_type = _file_type(file_path)
    text = ""

    if doc_type == "pdf":
        try:
            result = await _execute_block("pdf", {"file_path": file_path})
            text = _extract_text_from_result(result)
        except Exception as exc:
            logger.warning("PDF block failed for %s: %s", file_path, exc)
        if not text.strip():
            text = _parse_pdf_local(file_path)

    elif doc_type == "image":
        try:
            result = await _execute_block("ocr", {"file_path": file_path})
            text = _extract_text_from_result(result)
        except Exception as exc:
            logger.warning("OCR block failed for %s: %s", file_path, exc)

    elif doc_type == "docx":
        try:
            result = await _execute_block("pdf", {"file_path": file_path})
            text = _extract_text_from_result(result)
        except Exception as exc:
            logger.warning("DOCX block failed for %s: %s", file_path, exc)
        if not text.strip():
            text = _parse_docx_local(file_path)

    elif doc_type == "text":
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()

    else:
        # Try OCR / PDF block as a last resort
        try:
            result = await _execute_block("pdf", {"file_path": file_path})
            text = _extract_text_from_result(result)
        except Exception as exc:
            logger.warning("Fallback block failed for %s: %s", file_path, exc)

    return text


def _extract_text_from_result(result: Dict[str, Any]) -> str:
    """Pull text out of a Cerebrum-Blocks execute response."""
    if result.get("status") == "error":
        raise RuntimeError(result.get("result", "block error"))
    data = result.get("result", {})
    if isinstance(data, str):
        return data
    if isinstance(data, list):
        return "\n".join(str(x) for x in data)
    # Common shapes
    for key in ("text", "content", "extracted_text", "ocr_text", "pages"):
        if key in data:
            val = data[key]
            if isinstance(val, list):
                return "\n".join(str(x) for x in val)
            return str(val)
    return str(data)


def _parse_pdf_local(file_path: str) -> str:
    try:
        from pypdf import PdfReader
        reader = PdfReader(file_path)
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        logger.error("Local PDF parse failed for %s: %s", file_path, exc)
        return ""


def _parse_docx_local(file_path: str) -> str:
    try:
        import docx
        doc = docx.Document(file_path)
        return "\n".join(p.text for p in doc.paragraphs)
    except Exception as exc:
        logger.error("Local DOCX parse failed for %s: %s", file_path, exc)
        return ""


def chunk_text(text: str, max_chars: int = 800, overlap: int = 100) -> List[str]:
    """Simple semantic-ish chunking by paragraphs with overlap."""
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    chunks: List[str] = []
    current = ""

    for para in paragraphs:
        if len(current) + len(para) > max_chars and current:
            chunks.append(current.strip())
            # overlap from the end of the previous chunk
            if overlap and len(current) > overlap:
                current = current[-overlap:] + "\n" + para
            else:
                current = para
        else:
            current = (current + "\n" + para).strip()

    if current.strip():
        chunks.append(current.strip())

    if not chunks and text.strip():
        # fallback: hard split
        return [text[i:i + max_chars] for i in range(0, len(text), max_chars)]
    return chunks


async def embed_chunks(chunks: List[str]) -> List[List[float]]:
    """Get embeddings for chunks using the Cerebrum zvec block."""
    if not chunks:
        return []
    try:
        result = await _execute_block("zvec", {}, {"operation": "batch_embed", "texts": chunks})
        data = result.get("result", {})
        embeddings = data.get("embeddings", data.get("result", []))
        if embeddings and len(embeddings) == len(chunks):
            return embeddings
    except Exception as exc:
        logger.error("zvec embedding failed: %s", exc)
    # Fallback: zero vectors so the pipeline can continue
    logger.warning("Using zero-vector fallback for embeddings")
    dimensions = 256
    return [[0.0] * dimensions for _ in chunks]


async def process_upload(session_id: str, file_paths: List[str]):
    """Background pipeline: parse, chunk, embed and persist indexed data.

    Chunks are stored in a persistent ChromaDB collection per session. If ChromaDB
    fails, the pipeline falls back to an in-memory store so the user can still
    proceed (with a warning).
    """
    state = get_session(session_id)
    if not state:
        logger.error("process_upload called for unknown session %s", session_id)
        return

    state.upload.status = "processing"
    state.upload.progress = 0.05
    state.upload.message = "Parsing documents..."
    update_session(session_id, state)

    failed: List[str] = []
    all_chunks: List[str] = []
    source_files: List[str] = []

    total = len(file_paths)
    for idx, path in enumerate(file_paths):
        file_name = Path(path).name
        try:
            text = await parse_document(path)
            if text.strip():
                chunks = chunk_text(text)
                all_chunks.extend(chunks)
                source_files.extend([file_name] * len(chunks))
            else:
                failed.append(file_name)
        except Exception as exc:
            logger.exception("Failed to parse %s: %s", path, exc)
            failed.append(file_name)
        state.upload.progress = 0.1 + 0.3 * ((idx + 1) / total)
        update_session(session_id, state)

    state.corpus = "\n\n".join(all_chunks)
    state.chunks = all_chunks
    state.upload.total_chunks = len(all_chunks)
    state.upload.progress = 0.45
    state.upload.message = "Chunking text..."
    update_session(session_id, state)

    embeddings: List[List[float]] = []
    if all_chunks:
        state.upload.progress = 0.6
        state.upload.message = f"Embedding {len(all_chunks)} chunks..."
        update_session(session_id, state)
        embeddings = await embed_chunks(all_chunks)
        state.embeddings = embeddings

    state.upload.progress = 0.85
    state.upload.message = "Persisting vector index..."
    update_session(session_id, state)

    chroma_ok = store_chunks(session_id, all_chunks, embeddings, source_files=source_files)
    if chroma_ok:
        state.upload.indexed_collection = collection_name(session_id)
        state.upload.message = f"Indexed {len(all_chunks)} chunks"
    else:
        logger.warning("ChromaDB persistence failed for session %s; falling back to in-memory store", session_id)
        state.upload.indexed_collection = None
        state.upload.message = f"Indexed {len(all_chunks)} chunks (in-memory fallback; ChromaDB unavailable)"

    state.upload.progress = 1.0
    if failed and chroma_ok:
        state.upload.status = "completed_with_warnings"
    elif not chroma_ok:
        state.upload.status = "completed_with_warnings"
    else:
        state.upload.status = "completed"
    state.upload.failed_files = failed
    state.phase = 3
    state.updated_at = __import__("datetime").datetime.utcnow()
    update_session(session_id, state)

