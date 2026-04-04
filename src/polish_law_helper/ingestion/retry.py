"""Exponential backoff retry decorator for async HTTP calls."""

import asyncio
import functools
from typing import TypeVar, Callable, Any

import httpx
from rich.console import Console

_console = Console()

# Exceptions that should trigger a retry
RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ConnectTimeout,
    httpx.RemoteProtocolError,
    ConnectionError,
    TimeoutError,
)

# HTTP status codes worth retrying
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


def with_retry(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    backoff_factor: float = 2.0,
):
    """Decorator that adds exponential backoff retry to async functions.

    Args:
        max_retries: Maximum number of retry attempts.
        base_delay: Initial delay in seconds before first retry.
        max_delay: Maximum delay cap in seconds.
        backoff_factor: Multiplier applied to delay after each retry.
    """
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            for attempt in range(max_retries + 1):
                try:
                    result = await func(*args, **kwargs)

                    # Check for retryable HTTP status if result is a Response
                    if isinstance(result, httpx.Response) and result.status_code in RETRYABLE_STATUS_CODES:
                        if attempt < max_retries:
                            delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                            _console.print(
                                f"  [yellow]HTTP {result.status_code}, retry {attempt + 1}/{max_retries} "
                                f"za {delay:.1f}s...[/]"
                            )
                            await asyncio.sleep(delay)
                            continue
                        result.raise_for_status()

                    return result

                except RETRYABLE_EXCEPTIONS as e:
                    last_exception = e
                    if attempt < max_retries:
                        delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                        _console.print(
                            f"  [yellow]{type(e).__name__}, retry {attempt + 1}/{max_retries} "
                            f"za {delay:.1f}s...[/]"
                        )
                        await asyncio.sleep(delay)
                    else:
                        raise

                except httpx.HTTPStatusError as e:
                    if e.response.status_code in RETRYABLE_STATUS_CODES and attempt < max_retries:
                        delay = min(base_delay * (backoff_factor ** attempt), max_delay)
                        _console.print(
                            f"  [yellow]HTTP {e.response.status_code}, retry {attempt + 1}/{max_retries} "
                            f"za {delay:.1f}s...[/]"
                        )
                        await asyncio.sleep(delay)
                        last_exception = e
                    else:
                        raise

            raise last_exception  # type: ignore[misc]

        return wrapper
    return decorator
