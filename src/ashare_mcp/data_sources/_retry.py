"""Retry helpers for AkShare calls plus optional hard per-call deadlines.

Most AkShare interfaces are normal request/response calls, but a few Eastmoney
endpoints can occasionally leave an HTTP request hanging long enough to hit the
outer Dify/MCP deadline. ``call_ak`` therefore supports an opt-in hard timeout and
custom retry count. Normal calls keep the existing three-attempt behaviour; fragile
interactive endpoints can request one short attempt and fail gracefully instead of
blocking the whole agent.
"""

from __future__ import annotations

import functools
import queue
import threading
import time
from typing import Callable

import pandas as pd
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ._base import DataUnavailableError, EmptyDataError

try:
    from requests.exceptions import RequestException
except Exception:  # noqa: BLE001 - requests always present via akshare, but be safe
    class RequestException(Exception):  # type: ignore[no-redef]
        pass


RETRYABLE = (ConnectionError, TimeoutError, RequestException, EmptyDataError)


def _log_before_sleep(state) -> None:
    exc = state.outcome.exception() if state.outcome else None
    logger.warning(
        f"retry attempt {state.attempt_number} after "
        f"{type(exc).__name__ if exc else 'error'}: {exc}"
    )


def akshare_retry(fn: Callable, *, attempts: int = 3) -> Callable:
    """Wrap a callable with the standard AkShare retry policy."""

    attempt_count = max(1, int(attempts))

    @functools.wraps(fn)
    @retry(
        reraise=True,
        stop=stop_after_attempt(attempt_count),
        wait=wait_exponential(multiplier=1, min=1, max=4),
        retry=retry_if_exception_type(RETRYABLE),
        before_sleep=_log_before_sleep,
    )
    def wrapper(*a, **k):
        return fn(*a, **k)

    return wrapper


def _resolve(candidates: list[str]):
    import akshare as ak

    for name in candidates:
        fn = getattr(ak, name, None)
        if callable(fn):
            return name, fn
    return None, None


def _invoke_with_timeout(
    fn: Callable,
    kwargs: dict,
    *,
    timeout_seconds: float | None,
    call_name: str,
):
    """Invoke a blocking provider call with an optional hard deadline.

    A daemon thread is intentional here: Python cannot safely kill a blocked requests
    call in another thread, but the MCP request must still be allowed to return. The
    fragile endpoints use a single attempt, so at most one abandoned daemon thread is
    left behind for a timed-out invocation and it disappears once the upstream socket
    eventually unwinds.
    """
    if timeout_seconds is None:
        return fn(**kwargs)

    timeout = float(timeout_seconds)
    if timeout <= 0:
        return fn(**kwargs)

    result_queue: queue.Queue[tuple[bool, object]] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            result_queue.put((True, fn(**kwargs)))
        except Exception as exc:  # noqa: BLE001 - re-raised in caller thread
            try:
                result_queue.put((False, exc))
            except queue.Full:
                pass

    thread = threading.Thread(
        target=runner,
        name=f"akshare-{call_name}",
        daemon=True,
    )
    thread.start()

    try:
        succeeded, payload = result_queue.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError(f"{call_name} exceeded hard timeout {timeout:.1f}s") from exc

    if succeeded:
        return payload
    if isinstance(payload, Exception):
        raise payload
    raise RuntimeError(f"{call_name} failed without an exception payload")


def call_ak(
    candidates: str | list[str],
    *,
    empty_ok: bool = False,
    timeout_seconds: float | None = None,
    attempts: int = 3,
    **kwargs,
) -> pd.DataFrame:
    """Call the first existing AkShare function among ``candidates``.

    ``timeout_seconds`` is an optional hard per-attempt deadline. ``attempts`` defaults
    to three for ordinary calls; latency-sensitive Eastmoney endpoints can use one
    attempt so they fail fast and let the MCP return a structured unavailable result.

    Raises :class:`DataUnavailableError` if none of the names exist, or
    :class:`EmptyDataError` if the call returns nothing (unless ``empty_ok``).
    """
    names = [candidates] if isinstance(candidates, str) else list(candidates)
    name, fn = _resolve(names)
    if fn is None:
        raise DataUnavailableError(
            f"akshare exposes none of {names} (version {_akversion()}); interface may be renamed"
        )

    def _do() -> pd.DataFrame:
        t0 = time.time()
        df = _invoke_with_timeout(
            fn,
            kwargs,
            timeout_seconds=timeout_seconds,
            call_name=name,
        )
        dt = (time.time() - t0) * 1000
        n = 0 if df is None else (len(df) if hasattr(df, "__len__") else 1)
        logger.debug(f"akshare {name}({_fmt_kwargs(kwargs)}) -> {n} rows in {dt:.0f}ms")
        if df is None or (hasattr(df, "empty") and df.empty):
            if empty_ok:
                return df
            raise EmptyDataError(f"{name} returned empty for {_fmt_kwargs(kwargs)}")
        return df

    try:
        return akshare_retry(_do, attempts=attempts)()
    except EmptyDataError:
        raise  # already a DataSourceError -> tools treat as no_data
    except (ConnectionError, TimeoutError, RequestException) as e:
        raise DataUnavailableError(f"{name} network failure after retries: {e}") from e
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as e:
        raise DataUnavailableError(
            f"{name} upstream parse failure ({type(e).__name__}: {e})"
        ) from e


def _fmt_kwargs(kwargs: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in kwargs.items())


def _akversion() -> str:
    try:
        import akshare as ak

        return ak.__version__
    except Exception:  # noqa: BLE001
        return "?"
