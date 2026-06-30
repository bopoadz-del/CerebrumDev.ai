import os
import tempfile

import pytest

from app.core import upload_processor


class TestParsePdfWithMarker:
    def test_returns_empty_when_marker_not_installed(self, monkeypatch):
        """If marker-pdf is missing, the helper should gracefully fall back."""
        monkeypatch.setattr(upload_processor, "MARKER_ENABLED", True)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake pdf content")
            path = f.name
        try:
            # marker is an optional dependency; if it's absent we expect "".
            text = upload_processor._parse_pdf_with_marker(path)
            assert text == ""
        finally:
            os.unlink(path)

    def test_returns_empty_when_disabled(self, monkeypatch):
        monkeypatch.setattr(upload_processor, "MARKER_ENABLED", False)
        text = upload_processor._parse_pdf_with_marker("any.pdf")
        assert text == ""


class TestFileType:
    def test_pdf_extension(self):
        assert upload_processor._file_type("/docs/manual.pdf") == "pdf"

    def test_docx_extension(self):
        assert upload_processor._file_type("/docs/manual.docx") == "docx"

    def test_image_extension(self):
        assert upload_processor._file_type("/docs/scan.png") == "image"

    def test_text_extension(self):
        assert upload_processor._file_type("/docs/notes.txt") == "text"

    def test_unknown_extension(self):
        assert upload_processor._file_type("/docs/data.xyz") == "unknown"


class TestChunkText:
    def test_chunks_by_paragraph(self):
        text = "\n\n".join([f"Paragraph {i}" for i in range(20)])
        chunks = upload_processor.chunk_text(text, max_chars=100, overlap=10)
        assert len(chunks) > 1
        assert all(len(c) <= 100 for c in chunks)

    def test_single_long_paragraph_is_kept(self):
        # The chunker does not hard-split mid-paragraph; it emits one chunk.
        text = "a" * 500
        chunks = upload_processor.chunk_text(text, max_chars=200, overlap=0)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_whitespace_only_returns_empty(self):
        text = "   "
        chunks = upload_processor.chunk_text(text, max_chars=200, overlap=0)
        assert chunks == []


class TestParseDocument:
    @pytest.mark.asyncio
    async def test_parse_text_file(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as f:
            f.write("Hello from CerebrumDev.ai")
            path = f.name
        try:
            text = await upload_processor.parse_document(path)
            assert "Hello from CerebrumDev.ai" in text
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_pdf_prefers_marker_block_then_pdf_block(self, monkeypatch):
        """The PDF parser should try the marker block, then the pdf block, then fallbacks."""
        calls = []

        async def _fake_execute(block, input_data, params=None):
            calls.append(block)
            if block == "marker":
                return {"status": "success", "result": {"text": "# Markdown"}}
            raise RuntimeError("should not reach generic pdf block")

        monkeypatch.setattr(upload_processor, "_execute_block", _fake_execute)
        monkeypatch.setattr(upload_processor, "MARKER_ENABLED", True)

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            path = f.name
        try:
            text = await upload_processor.parse_document(path)
            assert "# Markdown" in text
            assert calls == ["marker"]
        finally:
            os.unlink(path)

    @pytest.mark.asyncio
    async def test_pdf_falls_back_to_local_when_blocks_fail(self, monkeypatch):
        """If all Cerebrum-Blocks calls fail, local parsers are attempted."""
        calls = []

        async def _fake_execute(block, input_data, params=None):
            calls.append(block)
            raise RuntimeError(f"{block} unavailable")

        monkeypatch.setattr(upload_processor, "_execute_block", _fake_execute)
        monkeypatch.setattr(upload_processor, "MARKER_ENABLED", True)
        monkeypatch.setattr(upload_processor, "_parse_pdf_with_marker", lambda path: "")
        monkeypatch.setattr(upload_processor, "_parse_pdf_local", lambda path: "local pdf text")

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4 fake")
            path = f.name
        try:
            text = await upload_processor.parse_document(path)
            assert text == "local pdf text"
            assert calls == ["marker", "pdf"]
        finally:
            os.unlink(path)
