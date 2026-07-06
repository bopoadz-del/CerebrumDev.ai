"""Tinker fine-tune orchestrator (Phase 4: Tinker)."""

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List

from fastapi import HTTPException

from ..models.session import TrainingJob
from .session_store import get_session, update_session, _apply_training_watchdog

logger = logging.getLogger(__name__)

TINKER_API_KEY = os.environ.get("TINKER_API_KEY")
TINKER_BASE_MODEL = os.environ.get("TINKER_BASE_MODEL", "Qwen/Qwen2.5-7B-Instruct")
TINKER_LORA_RANK = int(os.environ.get("TINKER_LORA_RANK", "16"))
TINKER_BATCH_SIZE = int(os.environ.get("TINKER_BATCH_SIZE", "4"))
TINKER_MAX_STEPS = int(os.environ.get("TINKER_MAX_STEPS", "10"))
TINKER_LEARNING_RATE = float(os.environ.get("TINKER_LEARNING_RATE", "1e-4"))
STORAGE_PATH = os.environ.get("STORAGE_PATH", "./storage")

# Cancel events for in-flight training threads.
_cancel_events: Dict[str, threading.Event] = {}


def _training_dir(session_id: str) -> Path:
    path = Path(STORAGE_PATH) / "sessions" / session_id / "training"
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_training_data(pairs: List[Dict[str, str]], min_pairs: int = 10) -> bool:
    """Validate that each pair has non-empty 'question' and 'answer'."""
    if not pairs:
        raise ValueError("Training data is empty.")
    if len(pairs) < min_pairs:
        raise ValueError(f"Need at least {min_pairs} Q&A pairs, got {len(pairs)}")
    for i, pair in enumerate(pairs):
        q = (pair.get("question") or "").strip()
        a = (pair.get("answer") or "").strip()
        if not q or not a:
            raise ValueError(f"Pair {i + 1} has empty question or answer")
    return True


def _write_jsonl(session_id: str, pairs: List[Dict[str, str]]) -> str:
    """Write session Q&A pairs to a JSONL file for audit/reproducibility."""
    path = _training_dir(session_id) / "scenarios.jsonl"
    with path.open("w", encoding="utf-8") as f:
        for pair in pairs:
            row = {
                "instruction": pair["question"].strip(),
                "response": pair["answer"].strip(),
                "source": f"session.{session_id}",
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return str(path)


def _build_datum_for_pair(tokenizer, instruction: str, response: str, max_tokens: int):
    """Tokenize one (instruction, response) into a Tinker Datum.

    Loss weights are 0 on prompt tokens and 1 on response tokens so only the
    assistant continuation contributes gradient.
    """
    import tinker
    from tinker.types import ModelInput

    user_msg = {"role": "user", "content": instruction}
    assistant_msg = {"role": "assistant", "content": response}

    prompt_ids = tokenizer.encode_message_with_chat_template(user_msg, [user_msg])
    response_ids = tokenizer.encode_message_with_chat_template(
        assistant_msg, [user_msg, assistant_msg]
    )

    full_ids = prompt_ids + response_ids
    if len(full_ids) > max_tokens:
        full_ids = full_ids[:max_tokens]
        if len(full_ids) <= len(prompt_ids):
            return None

    eos = getattr(tokenizer, "eos_token_id", None) or 0
    input_ids = full_ids[:-1]
    target_ids = full_ids[1:] + [eos][: 1 if len(full_ids) == len(input_ids) else 0]
    if len(target_ids) != len(input_ids):
        target_ids = full_ids[1:]
    weights = [0.0] * len(input_ids)
    prompt_len_minus_one = max(0, len(prompt_ids) - 1)
    for i in range(prompt_len_minus_one, len(weights)):
        weights[i] = 1.0
    if sum(weights) == 0:
        return None

    return tinker.Datum(
        model_input=ModelInput.from_ints(input_ids),
        loss_fn_inputs={"target_tokens": target_ids, "weights": weights},
    )


def _run_training(
    session_id: str,
    job_id: str,
    rows: List[Dict[str, str]],
    base_model: str,
    lora_rank: int,
    batch_size: int,
    max_steps: int,
    learning_rate: float,
) -> None:
    """Background thread: tokenize, train, and update session state."""
    cancel_event = _cancel_events.get(session_id)

    def _update(**kwargs) -> None:
        state = get_session(session_id)
        if not state:
            return
        for key, value in kwargs.items():
            setattr(state.training_job, key, value)
        state.training_job.updated_at = datetime.utcnow()
        update_session(session_id, state)

    _update(status="running", progress=0.0)

    try:
        import tinker

        service = tinker.ServiceClient()
        training_client = service.create_lora_training_client(
            base_model=base_model,
            rank=lora_rank,
        )
        tokenizer = training_client.get_tokenizer()
        info = training_client.get_info()
        logger.info(
            "Tinker training client ready for %s: model_id=%s lora_rank=%d",
            session_id,
            info.model_id,
            info.lora_rank,
        )

        data = []
        for row in rows:
            datum = _build_datum_for_pair(
                tokenizer,
                row["question"],
                row["answer"],
                max_tokens=4096,
            )
            if datum is not None:
                data.append(datum)

        if not data:
            raise ValueError("No valid training examples after tokenization")

        n = len(data)
        losses: List[float] = []
        from tinker import AdamParams

        for step in range(max_steps):
            if cancel_event and cancel_event.is_set():
                _update(status="cancelled", error="Cancelled by user")
                return

            start = (step * batch_size) % n
            end = start + batch_size
            if end <= n:
                batch = data[start:end]
            else:
                batch = data[start:] + data[: end - n]

            fb_future = training_client.forward_backward(batch, loss_fn="cross_entropy")
            fb_result = fb_future.result()
            opt_future = training_client.optim_step(
                AdamParams(learning_rate=learning_rate)
            )
            opt_future.result()

            metrics = getattr(fb_result, "metrics", None) or {}
            loss_sum = metrics.get("loss:sum") if isinstance(metrics, dict) else None
            avg = (loss_sum / len(batch)) if loss_sum is not None else None
            if avg is not None:
                losses.append(avg)

            progress = min((step + 1) / max_steps, 0.99)
            _update(progress=progress)

            if step % 10 == 0 or step == max_steps - 1:
                logger.info(
                    "Tinker training %s step %d/%d  loss/sample=%.4f",
                    session_id,
                    step + 1,
                    max_steps,
                    avg if avg is not None else float("nan"),
                )

        safe_model = base_model.replace("/", "_")
        checkpoint_name = f"checkpoints-{safe_model}-{session_id}"
        save_future = training_client.save_state(checkpoint_name)
        save_result = save_future.result()
        tinker_path = getattr(save_result, "path", None) or checkpoint_name

        _update(
            status="completed",
            progress=1.0,
            fine_tuned_model_id=tinker_path,
        )
        logger.info("Tinker training completed for %s: %s", session_id, tinker_path)

    except Exception as exc:
        logger.exception("Tinker training failed for %s", session_id)
        _update(status="failed", error=str(exc))
    finally:
        _cancel_events.pop(session_id, None)


async def start_training(session_id: str) -> TrainingJob:
    """Validate data and start a Tinker fine-tune job in the background."""
    if not TINKER_API_KEY:
        raise HTTPException(status_code=500, detail="TINKER_API_KEY is not configured")

    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    pairs = state.training_data
    try:
        validate_training_data(pairs)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    # Persist the JSONL dataset for audit/reproducibility.
    _write_jsonl(session_id, pairs)

    job_id = f"tinker-{uuid.uuid4().hex[:12]}"
    _cancel_events[session_id] = threading.Event()

    state.training_job = TrainingJob(
        provider="tinker",
        job_id=job_id,
        status="queued",
        dataset_size=len(pairs),
        started_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    update_session(session_id, state)

    thread = threading.Thread(
        target=_run_training,
        args=(
            session_id,
            job_id,
            pairs,
            TINKER_BASE_MODEL,
            TINKER_LORA_RANK,
            TINKER_BATCH_SIZE,
            TINKER_MAX_STEPS,
            TINKER_LEARNING_RATE,
        ),
        daemon=True,
    )
    thread.start()

    # Briefly yield so the thread can transition to running if possible.
    return state.training_job


async def get_training_status(session_id: str) -> TrainingJob:
    """Return the current training job state for the session."""
    state = get_session(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    if _apply_training_watchdog(state):
        update_session(session_id, state)
    return state.training_job


async def cancel_training(session_id: str) -> bool:
    """Cancel a running Tinker training job."""
    state = get_session(session_id)
    if not state or not state.training_job.job_id:
        return False

    event = _cancel_events.get(session_id)
    if event:
        event.set()

    state.training_job.status = "cancelled"
    state.training_job.updated_at = datetime.utcnow()
    update_session(session_id, state)
    _cancel_events.pop(session_id, None)
    return True
