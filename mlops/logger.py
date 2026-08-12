import json
import logging
import logging.handlers
import os
from datetime import datetime, timezone

import config
import database

os.makedirs(os.path.dirname(config.REQUEST_LOG_PATH), exist_ok=True)

_jsonl_logger = logging.getLogger("echomind.requests_jsonl")
_jsonl_logger.setLevel(logging.INFO)
_jsonl_logger.propagate = False
if not _jsonl_logger.handlers:
    _handler = logging.handlers.RotatingFileHandler(
        config.REQUEST_LOG_PATH, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _jsonl_logger.addHandler(_handler)


def log_llm_call(endpoint, status, latency_ms, model_name=None,
                  prompt_tokens=None, completion_tokens=None, error_message=None,
                  tool_calls_count=None, rag_hits=None):
    record = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "endpoint": endpoint,
        "model_name": model_name,
        "status": status,
        "latency_ms": round(latency_ms, 2) if latency_ms is not None else None,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "error_message": error_message,
        "prompt_version": config.PROMPT_VERSION,
        "tool_calls_count": tool_calls_count,
        "rag_hits": rag_hits,
    }

    _jsonl_logger.info(json.dumps(record))

    database.log_request(
        endpoint=endpoint,
        status=status,
        latency_ms=latency_ms,
        model_name=model_name,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        error_message=error_message,
        tool_calls_count=tool_calls_count,
        rag_hits=rag_hits,
    )

    return record
