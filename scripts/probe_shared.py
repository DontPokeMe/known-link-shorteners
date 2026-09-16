#!/usr/bin/env python3
"""
Shared HTTP probing policy for the two jobs that probe this dataset:

  * scripts/probe_domains.py       -- monthly release probe
  * scripts/maintain_shorteners.py -- weekly maintenance run

Both were independently given their own answer to the same problem: a large
fraction of these domains answer HTTP 429 when swept, which is not information
about whether the domain is alive. Keeping two answers in one repo meant the
weekly and monthly runs could disagree about the same domain in the same week.
This module is the single answer. Classification stays with each caller --
what counts as "inactive" is policy and differs between the two -- but the
transport rules below are shared.

Three things cause (or cure) the 429s, and all three live here:

  1. User-Agent. Measured over the full 1,533-domain list: a self-identifying
     agent string drew 429 from the CDNs these domains sit behind on ~22% of
     the list. Re-probed with a browser agent, 30 of 30 sampled returned their
     real 301/302. A liveness probe that is answered with 429 has learned
     nothing, so the probe presents as a browser.
  2. Per-host concurrency. Many of these domains are CNAME'd onto a shared
     backend, so sweeping at full concurrency is self-inflicted rate limiting.
     Concurrency is capped per resolved IP, independent of the worker count.
  3. Retry-After. A 429 is the server saying "come back later", so we come back
     when it asks, capped so one hostile host cannot stall a run.

And the shared verdict: a 429 that survives the retries is NOT evidence of
anything. It must never demote, quarantine or remove a domain -- it means
"unknown, re-check next run".
"""
from __future__ import annotations

import socket
import threading

# 1. What we present as.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# 2. How hard we hit one backend.
PER_HOST_CONCURRENCY = 3

# 3. How we back off.
BACKOFF_BASE = 1.0
MAX_RATE_LIMIT_WAIT = 30.0

# A shortener answering a redirect on its bare root is doing its job.
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
RATE_LIMITED = 429

DNS_MARKERS = (
    "nodename nor servname provided",
    "name or service not known",
    "nxdomain",
    "getaddrinfo failed",
    "temporary failure in name resolution",
    "failed to resolve",
)

_host_semaphores: dict[str, threading.Semaphore] = {}
_host_semaphores_lock = threading.Lock()


def probe_headers(user_agent: str | None = None) -> dict[str, str]:
    """Headers for a liveness probe. Override the agent only to test a theory."""
    return {**DEFAULT_HEADERS, "User-Agent": user_agent or BROWSER_UA}


def _get_host_semaphore(domain: str) -> threading.Semaphore:
    """One semaphore per resolved IP (or per domain if resolution fails)."""
    try:
        key = socket.gethostbyname(domain)
    except OSError:
        key = domain
    with _host_semaphores_lock:
        sem = _host_semaphores.get(key)
        if sem is None:
            sem = threading.Semaphore(PER_HOST_CONCURRENCY)
            _host_semaphores[key] = sem
        return sem


def is_dns_error(error: BaseException | str) -> bool:
    """True when a ConnectionError is really 'this name does not resolve'."""
    text = str(error).lower()
    return any(marker in text for marker in DNS_MARKERS)


def retry_after_seconds(response, attempt: int, base: float = BACKOFF_BASE,
                        cap: float = MAX_RATE_LIMIT_WAIT) -> float:
    """How long to wait before re-trying a 429.

    Honours the server's Retry-After when it sends a sane one, falls back to
    exponential backoff otherwise, and never waits longer than `cap`.
    """
    header = None
    try:
        header = response.headers.get("Retry-After")
    except Exception:  # noqa: BLE001 - a mock or a header-less response
        header = None
    wait = base * (2 ** attempt)
    if header is not None:
        try:
            wait = float(header)
        except (TypeError, ValueError):
            pass  # HTTP-date form: fall back to backoff rather than parse it
    return max(0.0, min(wait, cap))
