"""Retry decorator for akshare calls + a name-resolving ``call_ak`` helper.

Retries network-ish failures (and empty responses) up to 3 attempts with exponential
backoff (1s, 2s) then re-raises so the tool layer can fall back to baostock/tushare.
``call_ak`` accepts a list of candidate function names so the server keeps working
across akshare versions that rename interfaces.
"""

from __future__ import annotations

import functools
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


def akshare_retry(fn: Callable) -> Callable:
    """Wrap a callable with the standard akshare retry policy (3 attempts, 1s/2s backoff)."""

    @functools.wraps(fn)
    @retry(
        reraise=True,
        stop=stop_after_attempt(3),
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


def call_ak(candidates: str | list[str], *, empty_ok: bool = False, **kwargs) -> pd.DataFrame:
    """Call the first existing akshare function among ``candidates`` with retry.

    Raises :class:`DataUnavailableError` if none of the names exist, or
    :class:`EmptyDataError` if the call returns nothing (unless ``empty_ok``).
    """
    names = [candidates] if isinstance(candidates, str) else list(candidates)
    name, fn = _resolve(names)
    if fn is None:
        raise DataUnavailableError(
            f"akshare exposes none of {names} (version {_akversion()}); interface may be renamed"
        )

    @akshare_retry
    def _do() -> pd.DataFrame:
        t0 = time.time()
        df = fn(**kwargs)
        dt = (time.time() - t0) * 1000
        n = 0 if df is None else (len(df) if hasattr(df, "__len__") else 1)
        logger.debug(f"akshare {name}({_fmt_kwargs(kwargs)}) -> {n} rows in {dt:.0f}ms")
        if df is None or (hasattr(df, "empty") and df.empty):
            if empty_ok:
                return df
            raise EmptyDataError(f"{name} returned empty for {_fmt_kwargs(kwargs)}")
        return df

    try:
        return _do()
    except EmptyDataError:
        raise  # already a DataSourceError -> tools treat as no_data
    except (ConnectionError, TimeoutError, RequestException) as e:
        # Network failure after retries. Re-raise as DataSourceError so (a) tools with a
        # fallback (kline/financials -> baostock/tushare) actually trigger it, and (b) the
        # @guard layer reports data_source_unavailable (per PRD) instead of internal_error.
        raise DataUnavailableError(f"{name} network failure after retries: {e}") from e
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as e:
        # The only call inside _do() is the akshare function, so any of these comes from
        # akshare's INTERNAL parsing (an upstream format change, or a proxy/error page
        # returning non-JSON so akshare subscripts a None). Treat as an unavailable source,
        # not a caller bad_request — keeps degradation graceful and triggers fallbacks.
        raise DataUnavailableError(f"{name} upstream parse failure ({type(e).__name__}: {e})") from e


def _fmt_kwargs(kwargs: dict) -> str:
    return ", ".join(f"{k}={v!r}" for k, v in kwargs.items())


def _akversion() -> str:
    try:
        import akshare as ak

        return ak.__version__
    except Exception:  # noqa: BLE001
        return "?"
