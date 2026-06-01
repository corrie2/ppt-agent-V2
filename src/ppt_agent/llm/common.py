"""Shared LLM utilities: JSON extraction, repair, and retry logic."""
from __future__ import annotations

import json
import logging
import time
from typing import Any, Callable, TypeVar

import httpx

logger = logging.getLogger(__name__)

T = TypeVar("T")


def extract_json_object(content: str) -> dict[str, Any]:
    """Extract a JSON object from LLM output, handling markdown fences and common malformations."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()

    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM response did not contain a JSON object")

    json_str = text[start : end + 1]

    try:
        return json.loads(json_str, strict=False)
    except json.JSONDecodeError:
        pass

    repaired = _repair_json(json_str)
    try:
        return json.loads(repaired, strict=False)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Malformed JSON even after repair: {exc}") from exc


def llm_call_with_retry(
    *,
    url: str,
    headers: dict[str, str],
    body: dict[str, Any],
    timeout: float,
    max_retries: int,
    parse_response: Callable[[dict], T],
    fallback_to_none: bool = False,
    label: str = "LLM",
) -> T | None:
    """Call an LLM API with retry logic, exponential backoff, and comprehensive error handling.

    Args:
        url: API endpoint URL.
        headers: HTTP headers.
        body: Request body (JSON).
        timeout: Request timeout in seconds.
        max_retries: Number of retries after initial attempt.
        parse_response: Function to extract result from API response JSON.
        fallback_to_none: If True, return None on final failure instead of raising.
        label: Label for log messages (agent name or "Planner").
    """
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            if attempt > 0:
                delay = min(2 ** attempt, 15)
                logger.info("%s retry %d/%d after %ds", label, attempt, max_retries, delay)
                time.sleep(delay)

            logger.info("%s attempt %d/%d (timeout=%ds)", label, attempt + 1, max_retries + 1, timeout)
            response = httpx.post(url, headers=headers, json=body, timeout=timeout)
            response.raise_for_status()
            result = parse_response(response.json())
            logger.info("%s succeeded", label)
            return result

        except httpx.TimeoutException as exc:
            last_error = exc
            logger.warning("%s timed out (attempt %d/%d)", label, attempt + 1, max_retries + 1)

        except httpx.HTTPStatusError as exc:
            last_error = exc
            status = exc.response.status_code
            logger.warning("%s HTTP %d (attempt %d/%d)", label, status, attempt + 1, max_retries + 1)
            if status < 500 and status != 429:  # Don't retry client errors (except rate limit)
                if fallback_to_none:
                    return None
                raise

        except ValueError as exc:
            last_error = exc
            logger.warning("%s invalid JSON (attempt %d/%d): %s", label, attempt + 1, max_retries + 1, str(exc)[:200])

        except httpx.HTTPError as exc:
            last_error = exc
            logger.warning("%s network error (attempt %d/%d): %s", label, attempt + 1, max_retries + 1, type(exc).__name__)

        # If not last attempt, continue to retry
        if attempt < max_retries:
            continue

        # Last attempt failed
        if fallback_to_none:
            return None
        raise last_error  # type: ignore[misc]


# --- JSON repair state machine ---


def _repair_json(text: str) -> str:
    """Fix common LLM JSON errors using a state machine that respects string boundaries.

    Handles: missing commas, trailing commas, double commas, non-printable chars.
    Does NOT touch content inside string values.
    """
    text = "".join(ch for ch in text if ord(ch) >= 32 or ch in "\n\r\t")

    result: list[str] = []
    i, n = 0, len(text)
    in_string = False
    last_nws = ""  # last non-whitespace char emitted

    while i < n:
        ch = text[i]

        # --- Inside string: pass through, handle escapes ---
        if in_string:
            if ch == "\\":
                result.append(ch)
                i += 1
                if i < n:
                    result.append(text[i])
                    i += 1
                continue
            if ch == '"':
                result.append(ch)
                in_string = False
                last_nws = '"'
                i += 1
                continue
            result.append(ch)
            i += 1
            continue

        # --- Outside string ---

        if ch == '"':
            # Check if comma needed before this key
            if last_nws in ('"', "}", "]", "t", "f", "n") or (last_nws and last_nws.isdigit()):
                j = i + 1
                while j < n and text[j] != '"':
                    j += 2 if text[j] == "\\" else 1
                k = j + 1
                while k < n and text[k] in " \t\n\r":
                    k += 1
                if k < n and text[k] == ":" and last_nws not in ("{", "[", ":", ","):
                    result.append("," if "\n" in "".join(result[-3:]) else ", ")
            in_string = True
            result.append(ch)
            last_nws = '"'
            i += 1
            continue

        if ch in ("{", "["):
            if last_nws and last_nws not in ("{", "[", ":", ",", ""):
                result.append(", ")
            result.append(ch)
            last_nws = ch
            i += 1
            continue

        if ch in ("}", "]"):
            if last_nws == ",":  # trailing comma
                result.pop()
                while result and result[-1] in " \t\n\r":
                    result.pop()
                last_nws = result[-1] if result else ""
            result.append(ch)
            last_nws = ch
            i += 1
            continue

        if ch == ":":
            result.append(ch)
            last_nws = ":"
            i += 1
            continue

        if ch == ",":
            if last_nws == ",":  # double comma
                i += 1
                continue
            result.append(ch)
            last_nws = ","
            i += 1
            continue

        if ch in " \t\n\r":
            result.append(ch)
            i += 1
            continue

        # Number
        if ch == "-" or ch.isdigit():
            j = i
            while j < n and text[j] not in " \t\n\r,}:\"":
                j += 1
            token = text[i:j]
            if last_nws and last_nws not in ("{", "[", ":", ",", ""):
                result.append(", ")
            result.append(token)
            last_nws = token[-1]
            i = j
            continue

        # Keywords: true, false, null
        for kw in ("true", "false", "null"):
            if text[i : i + len(kw)] == kw:
                if last_nws and last_nws not in ("{", "[", ":", ",", ""):
                    result.append(", ")
                result.append(kw)
                last_nws = kw[0]  # 't'/'f'/'n' to match comma-insertion check
                i += len(kw)
                break
        else:
            i += 1  # skip unknown chars

    if in_string:
        result.append('"')

    return "".join(result)
