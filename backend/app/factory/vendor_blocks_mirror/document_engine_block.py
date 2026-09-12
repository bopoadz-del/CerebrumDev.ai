"""Document Engine Block — Platform wrapper for Parse → Reason → Map pipeline.

Canonical on-disk module for the platform wrapper. Factory CLONER / runtime
slice scanners resolve ``app.blocks.document_engine_block`` to this file
because the ``document_engine/`` package shadows ``document_engine.py``.

Exposes the document_engine block to the Cerebrum platform via:
  POST /execute { "block": "document_engine", "input": { "pdf_path": "..." } }

Integrates with platform blocks:
  - pdf  : PDF text extraction (PyMuPDF)
  - ocr  : Image / scanned PDF OCR fallback
"""

import os
import tempfile
from typing import Any, Dict, Optional
from app.core.universal_base import UniversalBlock


class DocumentEngineBlock(UniversalBlock):
    """Technical document reasoning engine.

    Ingests PDF / DOCX / XLSX, runs 8 semantic reasoning pipelines,
    and outputs structured YAML/JSON consumable by schedule_engine,
    cost_engine, and risk_engine downstream blocks.
    """

    name = "document_engine"
    version = "1.0.0"
    description = "Parse → Reason → Map pipeline for technical document intelligence"
    layer = 3
    tags = ["domain", "construction", "documents", "reasoning", "scheduling"]
    requires = ["pdf", "ocr"]

    default_config = {
        "extract_tables": True,
        "extract_glossary": True,
        "output_format": "yaml",
        "use_platform_pdf": True,
        "use_platform_ocr": True,
    }

    ui_schema = {
        "input": {
            "type": "files",
            "accept": [".pdf", ".docx", ".xlsx"],
            "placeholder": "Upload BOD, RFP, or spec documents...",
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "glossary", "type": "list", "label": "Glossary Terms"},
                {"name": "requirements", "type": "list", "label": "Requirements"},
                {"name": "constraints", "type": "list", "label": "Constraints"},
                {"name": "schedule_targets", "type": "list", "label": "Schedule Targets"},
                {"name": "equipment_specs", "type": "list", "label": "Equipment Specs"},
                {"name": "risks", "type": "list", "label": "Risks"},
                {"name": "downstream", "type": "object", "label": "Downstream Feed"},
            ],
        },
        "quick_actions": [
            {"icon": "📄", "label": "Analyze BOD", "prompt": "Extract glossary, constraints, and equipment lead times from Basis of Design"},
            {"icon": "📋", "label": "Analyze RFP", "prompt": "Extract requirements, schedule targets, and risks from RFP"},
            {"icon": "📊", "label": "Schedule Feed", "prompt": "Generate procurement activities and milestones for schedule_engine"},
            {"icon": "⚠️", "label": "Risk Register", "prompt": "Extract all risks and output risk_engine feed"},
        ],
    }

    async def _parse_with_platform_pdf(self, file_path: str) -> Optional[str]:
        """Use platform PDF block for text extraction (universal connector)."""
        pdf_block = self.get_dep("pdf")
        if pdf_block is None:
            return None
        result = await pdf_block.process({"file_path": file_path})
        if result.get("status") == "success":
            return result.get("text", "")
        return None

    async def _parse_with_platform_ocr(self, file_path: str) -> Optional[str]:
        """Use platform OCR block for image/scanned PDF fallback (universal connector)."""
        ocr_block = self.get_dep("ocr")
        if ocr_block is None:
            return None
        result = await ocr_block.process({"file_path": file_path})
        if result.get("status") == "success":
            return result.get("text", "")
        return None

    def _resolve_path(self, input_data: Any, params: Dict) -> Dict[str, Optional[str]]:
        """Resolve pdf/docx/xlsx paths from input_data and params."""
        data = input_data if isinstance(input_data, dict) else {}
        raw_path = ""

        if isinstance(input_data, str):
            raw_path = input_data
        elif isinstance(input_data, dict):
            raw_path = data.get("text") or data.get("input") or ""
            # Handle uploaded bytes per type
            for key, ext in [("pdf", ".pdf"), ("docx", ".docx"), ("xlsx", ".xlsx")]:
                file_bytes = data.get(key) or data.get(f"{key}_bytes") or data.get("bytes")
                if isinstance(file_bytes, bytes):
                    with tempfile.NamedTemporaryFile(delete=False, suffix=ext) as f:
                        f.write(file_bytes)
                        data[f"{key}_path"] = f.name

        # Also accept a generic file_path and infer type from extension
        generic_path = data.get("file_path") or data.get("path") or params.get("file_path")
        if generic_path:
            raw_path = raw_path or generic_path

        ext = os.path.splitext(raw_path)[1].lower() if raw_path else ""

        file_paths = {
            "pdf": (
                data.get("pdf_path") or data.get("pdf") or params.get("pdf_path")
                or (raw_path if ext == ".pdf" else None)
            ),
            "docx": (
                data.get("docx_path") or data.get("docx") or params.get("docx_path")
                or (raw_path if ext in (".docx", ".doc") else None)
            ),
            "xlsx": (
                data.get("xlsx_path") or data.get("xlsx") or params.get("xlsx_path")
                or (raw_path if ext in (".xlsx", ".xls") else None)
            ),
        }
        return file_paths

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        """Main entry point — run the 3-layer pipeline."""
        params = params or {}

        file_paths = self._resolve_path(input_data, params)

        if not any(file_paths.values()):
            return {
                "status": "error",
                "error": "No input files provided (pdf/docx/xlsx). Pass file_path as pdf_path, docx_path, or xlsx_path.",
            }

        # Validate paths exist
        for ftype, fpath in file_paths.items():
            if fpath and not os.path.exists(fpath):
                return {"status": "error", "error": f"{ftype.upper()} file not found: {fpath}"}

        try:
            import yaml
        except ImportError:
            return {"status": "error", "error": "PyYAML not installed. Run: pip install pyyaml"}

        # Load config
        try:
            project_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
            # Prefer the migrated app/blocks/document_engine/ location.
            config_path = os.path.join(project_root, "app", "blocks", "document_engine", "config.yaml")
            if not os.path.exists(config_path):
                # Fallback to the legacy blocks/document_engine/ location.
                config_path = os.path.join(project_root, "blocks", "document_engine", "config.yaml")
            if not os.path.exists(config_path):
                config_path = os.path.join(os.path.dirname(__file__), "..", "..", "blocks", "document_engine", "config.yaml")
                config_path = os.path.abspath(config_path)

            if os.path.exists(config_path):
                with open(config_path, "r") as f:
                    full_config = yaml.safe_load(f)
                config = full_config.get("document_engine", full_config)
            else:
                config = {}
        except Exception as e:
            return {"status": "error", "error": f"Failed to load config: {e}"}

        # ------------------------------------------------------------------
        # Layer 1: Parse — use platform blocks when available, fallback to own parsers
        # ------------------------------------------------------------------
        documents = []
        parse_errors = []

        # PDF → platform pdf block → fallback to own PDFParser
        if file_paths.get("pdf"):
            pdf_text = None
            if self.config.get("use_platform_pdf", True):
                pdf_text = await self._parse_with_platform_pdf(file_paths["pdf"])

            if pdf_text is not None:
                try:
                    from app.blocks.document_engine.parsers.pdf_parser import PDFDocument
                    doc = PDFDocument(source=file_paths["pdf"], text=pdf_text)
                    documents.append(doc)
                except ImportError as e:
                    parse_errors.append(f"PDFDocument class import failed: {e}")
            else:
                try:
                    from app.blocks.document_engine.parsers.pdf_parser import PDFParser
                    parser = PDFParser(config)
                    documents.append(parser.parse(file_paths["pdf"]))
                except ImportError as e:
                    parse_errors.append(f"PDF parser unavailable: {e}")
                except Exception as e:
                    parse_errors.append(f"PDF parse failed: {e}")

        # DOCX → own parser (no platform block available)
        if file_paths.get("docx"):
            try:
                from app.blocks.document_engine.parsers.docx_parser import DOCXParser
                parser = DOCXParser(config)
                documents.append(parser.parse(file_paths["docx"]))
            except ImportError as e:
                parse_errors.append(f"DOCX parser unavailable: {e}")
            except Exception as e:
                parse_errors.append(f"DOCX parse failed: {e}")

        # XLSX → own parser (no platform block available)
        if file_paths.get("xlsx"):
            try:
                from app.blocks.document_engine.parsers.xlsx_parser import XLSXParser
                parser = XLSXParser(config)
                documents.append(parser.parse(file_paths["xlsx"]))
            except ImportError as e:
                parse_errors.append(f"XLSX parser unavailable: {e}")
            except Exception as e:
                parse_errors.append(f"XLSX parse failed: {e}")

        if not documents and parse_errors:
            return {
                "status": "error",
                "error": "; ".join(parse_errors),
            }

        # ------------------------------------------------------------------
        # Layer 2: Reason
        # ------------------------------------------------------------------
        try:
            from app.blocks.document_engine.reasoner import DocumentReasoner
            reasoner = DocumentReasoner(config)
            reasoned = reasoner.reason(documents)
        except ImportError as e:
            return {"status": "error", "error": f"Document reasoner unavailable: {e}"}
        except Exception as e:
            return {"status": "error", "error": f"Document reasoning failed: {e}"}

        # ------------------------------------------------------------------
        # Layer 3: Map
        # ------------------------------------------------------------------
        try:
            from app.blocks.document_engine.mapper import DocumentMapper
            mapper = DocumentMapper(config)
            structured = mapper.map_to_structured(reasoned)
        except ImportError as e:
            return {"status": "error", "error": f"Document mapper unavailable: {e}"}
        except Exception as e:
            return {"status": "error", "error": f"Document mapping failed: {e}"}

        result = structured.to_dict()
        result["status"] = "success"
        result["documents_parsed"] = len(documents)
        result["platform_blocks_used"] = []
        if self.config.get("use_platform_pdf") and self.get_dep("pdf"):
            result["platform_blocks_used"].append("pdf")
        if self.config.get("use_platform_ocr") and self.get_dep("ocr"):
            result["platform_blocks_used"].append("ocr")
        if parse_errors:
            result["parse_warnings"] = parse_errors
        return result
