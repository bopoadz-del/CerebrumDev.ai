"""RAG Knowledge Block v2 — Ask questions across all stored captures and swarm runs.

Retrieval-Augmented Generation:
  Question → Vector search → Top-k chunks → LLM synthesis → Cited answer
"""

import os
import json
from typing import Any, Dict, List, Optional

from app.core.universal_base import UniversalBlock
from app.core.typed_block import TypedBlock, Schema, ContentType
from app.core import vector_store
from app.core.answer_contract import (
    SOURCE_CLASS_KEY,
    AnswerContractViolation,
    coverage_line,
    emit_chunk,
    forbid_does_not_exist_claim,
    render_source_class,
    source_class_of,
)


class KnowledgeBlock(TypedBlock):
    """Knowledge retrieval and synthesis engine."""

    name = "knowledge"
    version = "2.0.0"
    description = "Ask questions across stored captures and swarm runs using RAG"
    layer = 3
    tags = ["knowledge", "rag", "retrieval", "qa", "memory", "ai"]
    requires = ["vector_search"]

    input_schema = Schema(
        content_type=ContentType.TEXT,
        required_fields=[],
        optional_fields=["question", "query", "collections", "top_k", "llm_provider", "n_docs"],
        format_hints={}
    )

    # Only "status" is guaranteed by every action (ask/search/summarize);
    # "answer" is produced by ask only, so the contract matches reality.
    output_schema = Schema(
        content_type=ContentType.JSON,
        required_fields=["status"],
        optional_fields=["answer", "sources", "confidence", "query", "chunks_retrieved", "results", "summary"],
        format_hints={}
    )

    accepted_input_types = ["Text", "Question", "JSON"]
    produced_output_types = ["JSON", "Answer", "SearchResults"]

    async def execute(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        action = params.get("action") if isinstance(params, dict) else None
        if isinstance(input_data, str):
            input_data = {"question": input_data}
        return await super().execute(input_data, params)

    def validate_input(self, data: Any) -> Dict[str, Any]:
        if isinstance(data, dict) and data.get("action") in {"health", "status", "list", "history", "get", "unschedule", "broadcast", "search", "summarize", "structure", "execute_async"}:
            return {"valid": True, "errors": [], "warnings": [], "data": data}
        return super().validate_input(data)

    default_config = {
        "llm_provider": "kimi",
        "vector_db_url": os.getenv("VECTOR_DB_URL", "http://localhost:8001"),
        "default_top_k": 5,
        "default_collections": ["cerebrum_captures", "cerebrum_swarm"],
    }

    ui_schema = {
        "input": {
            "type": "text",
            "accept": None,
            "placeholder": "Ask a question about your knowledge base...",
            "multiline": True,
        },
        "output": {
            "type": "json",
            "fields": [
                {"name": "answer", "type": "text", "label": "Answer"},
                {"name": "sources", "type": "json", "label": "Sources"},
                {"name": "confidence", "type": "percentage", "label": "Confidence"},
            ],
        },
        "params": [
            {
                "name": "action",
                "type": "select",
                "label": "Action",
                "options": ["ask", "search", "summarize"],
                "default": "ask",
            },
            {"name": "top_k", "type": "number", "label": "Top K", "default": 5},
        ],
        "quick_actions": [
            {"icon": "❓", "label": "Ask Question", "prompt": "Ask about your data"},
            {"icon": "🔍", "label": "Search Only", "prompt": "Search knowledge base"},
            {"icon": "📝", "label": "Summarize", "prompt": "Summarize recent captures"},
        ],
    }

    async def process(self, input_data: Any, params: Dict = None) -> Dict:
        params = params or {}
        action = params.get("action", "ask")

        if action == "ask":
            return await self._ask(input_data, params)
        elif action == "search":
            return await self._search_only(input_data, params)
        elif action == "summarize":
            return await self._summarize_collection(params)
        else:
            return {"status": "error", "error": f"Unknown action: {action}"}

    # ── Core RAG: Ask ──────────────────────────────────────────────────────────

    async def _ask(self, question: Any, params: Dict) -> Dict:
        # Accept dict with 'query' or 'question' key
        if isinstance(question, dict):
            query = question.get("question") or question.get("query") or str(question)
        else:
            query = question if isinstance(question, str) else str(question)
        top_k = params.get("top_k", self.config.get("default_top_k", 5))
        llm_provider = params.get("llm_provider", self.config.get("llm_provider", "kimi"))

        # Prefer the pgvector-backed project corpus when a project_id is given.
        project_id = params.get("project_id")
        if project_id:
            return await self._ask_pgvector(
                query, str(project_id), top_k, llm_provider, params
            )

        # Legacy collection path (vector_search dependency / HTTP vector DB)
        collections = params.get("collections") or self.config.get("default_collections", ["cerebrum_captures"])
        all_chunks = []
        for collection in collections:
            results = await self._search_vectors(query, collection, top_k)
            for r in results:
                r["collection"] = collection
            all_chunks.extend(results)

        if not all_chunks:
            return {
                "status": "success",
                "answer": "I don't have any relevant information in my knowledge base.",
                "sources": [],
                "confidence": 0.0,
            }

        # 2. Deduplicate and rank
        all_chunks.sort(key=lambda x: x.get("score", 0), reverse=True)
        top_chunks = all_chunks[:top_k]

        # 3. Build context for LLM
        context_parts = []
        source_map = {}
        for idx, chunk in enumerate(top_chunks):
            doc_id = chunk.get("id", f"chunk_{idx}")
            text = chunk.get("text", chunk.get("document", ""))
            meta = chunk.get("metadata", {}) if isinstance(chunk.get("metadata"), dict) else {}
            classified = source_class_of(chunk) or source_class_of(meta)
            class_line = ""
            if classified:
                try:
                    emitted = emit_chunk({**chunk, "metadata": {**meta, SOURCE_CLASS_KEY: classified}})
                    meta = emitted.get("metadata", meta)
                    class_line = render_source_class(emitted)
                except AnswerContractViolation:
                    class_line = ""
            label = f"[Source {idx+1}]"
            if class_line:
                label = f"{label} {class_line}"
            context_parts.append(f"{label} {text}")
            source_entry = {
                "id": doc_id,
                "collection": chunk.get("collection", "unknown"),
                "score": chunk.get("score", 0),
                "metadata": meta,
            }
            if classified:
                source_entry[SOURCE_CLASS_KEY] = classified
                source_entry["source_class_line"] = class_line
            source_map[f"Source {idx+1}"] = source_entry

        context = "\n\n".join(context_parts)

        # 4. Synthesize with LLM
        system_prompt = (
            "You are a knowledge synthesis engine. Answer the user's question "
            "using ONLY the provided sources. Cite sources as [Source N]. "
            "If the sources don't contain the answer, say so. Be concise."
        )
        user_prompt = f"""Question: {query}

Sources:
{context}

Answer the question and cite sources.
"""
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        answer_data = await self._llm_chat(messages, llm_provider)
        if answer_data.get("status") == "error":
            return {
                "status": "error",
                "error": answer_data.get("error", "LLM synthesis failed"),
                "sources": [],
                "confidence": 0.0,
            }
        answer_text = answer_data.get("content", "")
        answer_text = self._honest_answer(answer_text, params)

        # 5. Extract cited sources from answer
        cited_sources = self._extract_citations(answer_text, source_map)

        payload = {
            "status": "success",
            "answer": answer_text,
            "sources": cited_sources,
            "confidence": self._compute_confidence(top_chunks),
            "query": query,
            "chunks_retrieved": len(top_chunks),
        }
        payload.update(self._coverage_fields(params, retrieved=len(top_chunks)))
        return payload

    async def _ask_pgvector(
        self,
        query: str,
        project_id: str,
        top_k: int,
        llm_provider: str,
        params: Dict,
    ) -> Dict:
        """RAG path backed by the pgvector store.

        When no LLM API key is configured, returns an honest grounded answer
        built directly from the highest-scoring retrieved chunk.
        """
        results = await vector_store.search_vectors(
            project_id, query, top_k=top_k, threshold=0.3
        )
        if not results:
            return {
                "status": "success",
                "answer": "I don't have any relevant information in my knowledge base.",
                "sources": [],
                "confidence": 0.0,
                "project_id": project_id,
            }

        top = results[0]
        sources = [self._classified_source(r) for r in results]

        # Avoid cloud LLM dependency in local/no-key mode. Only emit a raw
        # chunk as the answer when the retrieval score is high enough that the
        # chunk is plausibly on-topic; otherwise answer honestly that we have
        # no relevant information.
        if not self._provider_has_key(llm_provider):
            grounded_confidence_threshold = 0.6
            if top["score"] < grounded_confidence_threshold:
                return {
                    "status": "success",
                    "answer": "I don't have any relevant information in my knowledge base.",
                    "sources": [],
                    "confidence": top["score"],
                    "query": query,
                    "chunks_retrieved": len(results),
                    "project_id": project_id,
                    "grounded": True,
                }
            return {
                "status": "success",
                "answer": top["content"],
                "sources": sources,
                "confidence": top["score"],
                "query": query,
                "chunks_retrieved": len(results),
                "project_id": project_id,
                "grounded": True,
            }

        context = "\n\n".join(
            self._source_context_line(i + 1, r) for i, r in enumerate(results)
        )
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a knowledge synthesis engine. Answer using ONLY the "
                    "provided sources. Cite sources as [Source N]. If the sources "
                    "don't contain the answer, say so. Be concise."
                ),
            },
            {
                "role": "user",
                "content": f"Question: {query}\n\nSources:\n{context}\n\nAnswer the question and cite sources.",
            },
        ]
        answer_data = await self._llm_chat(messages, llm_provider)
        if answer_data.get("status") == "error":
            return {
                "status": "error",
                "error": answer_data.get("error", "LLM synthesis failed"),
                "sources": [],
                "confidence": 0.0,
                "project_id": project_id,
            }
        payload = {
            "status": "success",
            "answer": self._honest_answer(answer_data.get("content", ""), params),
            "sources": sources,
            "confidence": top["score"],
            "query": query,
            "chunks_retrieved": len(results),
            "project_id": project_id,
        }
        payload.update(self._coverage_fields(params, retrieved=len(results)))
        return payload

    # ── Search Only ────────────────────────────────────────────────────────────

    async def _search_only(self, query: Any, params: Dict) -> Dict:
        query_str = query if isinstance(query, str) else str(query)
        top_k = params.get("top_k", self.config.get("default_top_k", 5))
        project_id = params.get("project_id")

        if project_id:
            results = await vector_store.search_vectors(
                project_id, query_str, top_k=top_k, threshold=0.3
            )
            return {
                "status": "success",
                "query": query_str,
                "project_id": project_id,
                "results": results[:top_k],
                "total_found": len(results),
            }

        collections = params.get("collections") or self.config.get("default_collections", ["cerebrum_captures"])

        all_results = []
        for collection in collections:
            results = await self._search_vectors(query_str, collection, top_k)
            for r in results:
                r["collection"] = collection
            all_results.extend(results)

        all_results.sort(key=lambda x: x.get("score", 0), reverse=True)
        return {
            "status": "success",
            "query": query_str,
            "results": all_results[:top_k],
            "total_found": len(all_results),
        }

    # ── Summarize Collection ───────────────────────────────────────────────────

    async def _summarize_collection(self, params: Dict) -> Dict:
        collection = params.get("collection", "cerebrum_captures")
        n_docs = params.get("n_docs", 10)
        llm_provider = params.get("llm_provider", self.config.get("llm_provider", "kimi"))

        # Get recent documents (query with empty string or list operation)
        docs = await self._list_collection_docs(collection, n_docs)
        if not docs:
            return {"status": "success", "summary": "No documents in collection.", "documents_summarized": 0}

        combined = "\n\n---\n\n".join([d.get("text", "")[:1000] for d in docs])
        messages = [
            {"role": "system", "content": "Summarize the following documents into 3-5 bullet points."},
            {"role": "user", "content": combined},
        ]

        result = await self._llm_chat(messages, llm_provider)
        if result.get("status") == "error":
            return {
                "status": "error",
                "error": result.get("error", "LLM summarization failed"),
                "documents_summarized": len(docs),
                "collection": collection,
            }
        summary = result.get("content", "")

        return {
            "status": "success",
            "summary": summary,
            "documents_summarized": len(docs),
            "collection": collection,
        }

    # ── Vector Search ──────────────────────────────────────────────────────────

    async def _search_vectors(self, query: str, collection: str, n_results: int) -> List[Dict]:
        vector_block = self.get_dep("vector_search")

        if vector_block:
            try:
                result = await vector_block.process(
                    query,
                    {"operation": "search", "collection": collection, "n_results": n_results},
                )
                if result.get("status") == "success":
                    return result.get("result", {}).get("results", [])
            except Exception:
                pass

        # Fallback HTTP
        import httpx
        db_url = self.config.get("vector_db_url", "")
        if not db_url:
            return []
        try:
            payload = {"collection": collection, "query": query, "n_results": n_results}
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(f"{db_url}/api/v1/collections/query", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    return data.get("results", [])
        except Exception:
            pass
        return []

    async def _list_collection_docs(self, collection: str, n: int) -> List[Dict]:
        """List recent documents from a collection."""
        vector_block = self.get_dep("vector_search")
        if vector_block:
            try:
                result = await vector_block.process(
                    None,
                    {"operation": "search", "collection": collection, "n_results": n},
                )
                if result.get("status") == "success":
                    return result.get("result", {}).get("results", [])
            except Exception:
                pass
        return []

    # ── LLM Routing ────────────────────────────────────────────────────────────

    async def _llm_chat(self, messages: List[Dict], provider: str = "kimi") -> Dict:
        import httpx
        from app.core.llm_config import _llm_config

        cfg = _llm_config()  # Kimi (Moonshot), OpenAI-compatible — the only provider
        api_key = os.getenv(cfg["env_key"], "") if cfg["env_key"] else ""
        if not api_key:
            return {"status": "error", "error": "Kimi API key not configured (set KIMI_API_KEY)"}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": cfg["default_model"],
            "messages": messages,
            "temperature": 0.3,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(cfg["url"], headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return {"content": data["choices"][0]["message"]["content"]}

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _classified_source(self, result: Dict) -> Dict:
        """A retrieved row with ``source_class`` lifted onto the citation."""
        meta = result.get("metadata") if isinstance(result.get("metadata"), dict) else {}
        classified = source_class_of(result) or source_class_of(meta)
        source = {
            "chunk_id": result.get("chunk_id") or result.get("id"),
            "document_id": result.get("document_id"),
            "score": result.get("score"),
            "metadata": meta,
        }
        if classified:
            try:
                emitted = emit_chunk({**result, "metadata": {**meta, SOURCE_CLASS_KEY: classified}})
                source[SOURCE_CLASS_KEY] = emitted[SOURCE_CLASS_KEY]
                source["source_class_line"] = render_source_class(emitted)
                source["metadata"] = emitted.get("metadata", meta)
            except AnswerContractViolation:
                pass
        return source

    def _source_context_line(self, index: int, result: Dict) -> str:
        classified = source_class_of(result) or source_class_of(result.get("metadata") or {})
        prefix = f"[Source {index}]"
        if classified:
            prefix = f"{prefix} source_class: {classified}"
        return f"{prefix} {result.get('content', '')}"

    def _coverage_fields(self, params: Dict, retrieved: int) -> Dict:
        """K3: emit ``N of M indexed`` when the caller knows the corpus size."""
        total = params.get("corpus_total")
        if total is None and isinstance(params.get("input"), dict):
            total = params["input"].get("corpus_total")
        if total is None:
            return {
                "coverage_line": None,
                "source_class_rendered": True,
            }
        try:
            total_int = int(total)
            indexed = params.get("documents_indexed")
            indexed_int = int(indexed) if indexed is not None else retrieved
            return {
                "coverage_line": coverage_line(indexed_int, total_int),
                "source_class_rendered": True,
            }
        except (AnswerContractViolation, TypeError, ValueError):
            return {
                "coverage_line": None,
                "source_class_rendered": True,
            }

    def _honest_answer(self, answer_text: str, params: Dict) -> str:
        """K3: a does-not-exist claim is refused below 100% coverage."""
        total = params.get("corpus_total")
        indexed = params.get("documents_indexed")
        try:
            indexed_int = int(indexed) if indexed is not None else 0
            total_int = int(total) if total is not None else None
            return forbid_does_not_exist_claim(answer_text, indexed_int, total_int)
        except AnswerContractViolation:
            return (
                "I could not confirm this in the indexed sources "
                "(coverage is below 100%; a does-not-exist claim is prohibited)."
            )
        except (TypeError, ValueError):
            try:
                return forbid_does_not_exist_claim(answer_text, 0, None)
            except AnswerContractViolation:
                return (
                    "I could not confirm this in the indexed sources "
                    "(coverage is below 100%; a does-not-exist claim is prohibited)."
                )

    def _provider_has_key(self, provider: str = "kimi") -> bool:
        """Return True when Kimi (the only provider) has an API key available."""
        return bool(os.getenv("KIMI_API_KEY", "") or os.getenv("MOONSHOT_API_KEY", ""))

    def _extract_citations(self, answer: str, source_map: Dict) -> List[Dict]:
        """Extract which sources were actually cited in the answer."""
        cited = []
        for label, meta in source_map.items():
            if label in answer:
                cited.append(meta)
        return cited

    def _compute_confidence(self, chunks: List[Dict]) -> float:
        if not chunks:
            return 0.0
        scores = [c.get("score", 0) for c in chunks if c.get("score") is not None]
        if not scores:
            return 0.5
        avg = sum(scores) / len(scores)
        return round(min(avg, 1.0), 2)
