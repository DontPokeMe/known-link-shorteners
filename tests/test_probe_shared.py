"""Both probes must agree about rate limiting. These pin the shared policy."""
import socket
import threading
from unittest.mock import MagicMock, patch

import pytest

import maintain_shorteners as ms
import probe_domains as pd
import probe_shared as ps


def _resp(status, headers=None):
    m = MagicMock()
    m.status_code = status
    m.headers = headers or {}
    m.close = MagicMock()
    return m


class TestSharedPolicyIsActuallyShared:
    """Regression guard: the two probes drifted apart once already."""

    def test_both_probes_send_the_same_user_agent(self):
        seen = {}

        def capture(url, **kw):
            seen["monthly"] = (kw.get("headers") or {}).get("User-Agent")
            return _resp(200)

        with patch("probe_domains.requests.get", side_effect=capture):
            pd._probe_one("d.example", "shortener")
        assert seen["monthly"] == ps.BROWSER_UA
        assert ms.USER_AGENT == ps.BROWSER_UA

    def test_both_probes_share_one_host_semaphore_table(self):
        with patch("socket.gethostbyname", return_value="1.2.3.4"):
            assert pd._get_host_semaphore("a.example") is ms._get_host_semaphore("b.example")

    def test_both_probes_agree_a_persistent_429_is_not_a_verdict(self):
        """A rate-limited domain must never be demoted or quarantined."""
        with patch("probe_domains.requests.get", return_value=_resp(429, {"Retry-After": "0"})), \
             patch("probe_domains.time.sleep"):
            monthly = pd._probe_one("d.example", "shortener")
        with patch.object(ms, "_request", return_value=_resp(429, {"Retry-After": "0"})), \
             patch("maintain_shorteners.time.sleep"):
            weekly = ms._check_domain("d.example", "shortener", 1.0)

        assert monthly.classification == "retry_later"
        assert weekly.verdict == "review"
        # Neither outcome may remove a domain from the active dataset.
        assert monthly.classification not in ("inactive",)
        assert weekly.verdict != "dead"


class TestRetryAfter:
    def test_honours_a_numeric_retry_after(self):
        assert ps.retry_after_seconds(_resp(429, {"Retry-After": "7"}), 0) == 7.0

    def test_falls_back_to_backoff_on_an_http_date(self):
        got = ps.retry_after_seconds(_resp(429, {"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"}), 1)
        assert got == ps.BACKOFF_BASE * 2

    def test_falls_back_to_backoff_when_absent(self):
        assert ps.retry_after_seconds(_resp(429), 2) == ps.BACKOFF_BASE * 4

    def test_never_waits_longer_than_the_cap(self):
        assert ps.retry_after_seconds(_resp(429, {"Retry-After": "99999"}), 0) == ps.MAX_RATE_LIMIT_WAIT

    def test_never_negative(self):
        assert ps.retry_after_seconds(_resp(429, {"Retry-After": "-5"}), 0) == 0.0


class TestWeeklyRateLimitHandling:
    def test_429_then_recovers_is_alive(self):
        seq = [_resp(429, {"Retry-After": "0"}), _resp(200)]
        with patch.object(ms, "_request", side_effect=seq), \
             patch("maintain_shorteners.time.sleep"):
            r = ms._check_domain("d.example", "shortener", 1.0)
        assert r.verdict == "alive" and r.status == 200

    def test_429_retries_before_giving_up(self):
        calls = {"n": 0}

        def always(*a, **kw):
            calls["n"] += 1
            return _resp(429, {"Retry-After": "0"})

        with patch.object(ms, "_request", side_effect=always), \
             patch("maintain_shorteners.time.sleep") as slept:
            r = ms._check_domain("d.example", "shortener", 1.0)
        assert r.verdict == "review" and r.status == ps.RATE_LIMITED
        assert calls["n"] == 4          # 2 attempts x 2 schemes
        assert slept.called

    def test_dns_error_is_still_dead(self):
        err = ms.requests.exceptions.ConnectionError("Failed to resolve: Name or service not known")
        with patch.object(ms, "_request", side_effect=err):
            r = ms._check_domain("d.example", "shortener", 1.0)
        assert r.verdict == "dead" and r.status == "dns_error"

    def test_connect_error_is_review_not_dead(self):
        err = ms.requests.exceptions.ConnectionError("Connection refused")
        with patch.object(ms, "_request", side_effect=err):
            r = ms._check_domain("d.example", "shortener", 1.0)
        assert r.verdict == "review" and r.status == "connect_error"

    @pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
    def test_redirects_are_alive(self, code):
        with patch.object(ms, "_request", return_value=_resp(code)):
            r = ms._check_domain("d.example", "shortener", 1.0)
        assert r.verdict == "alive"

    def test_404_confirmed_by_get_is_dead(self):
        with patch.object(ms, "_request", return_value=_resp(404)):
            r = ms._check_domain("d.example", "shortener", 1.0)
        assert r.verdict == "dead" and r.status == "404"


class TestHostSemaphore:
    @pytest.fixture(autouse=True)
    def _clear(self):
        ps._host_semaphores.clear()
        yield
        ps._host_semaphores.clear()

    def test_same_ip_shares_a_semaphore(self):
        with patch("socket.gethostbyname", return_value="1.2.3.4"):
            assert ps._get_host_semaphore("a.example") is ps._get_host_semaphore("b.example")

    def test_different_ips_do_not(self):
        with patch("socket.gethostbyname", side_effect=lambda d: "1.2.3.4" if d == "a.example" else "5.6.7.8"):
            assert ps._get_host_semaphore("a.example") is not ps._get_host_semaphore("b.example")

    def test_resolution_failure_falls_back_to_per_domain(self):
        with patch("socket.gethostbyname", side_effect=socket.gaierror("nope")):
            assert isinstance(ps._get_host_semaphore("nope.example"), threading.Semaphore)

    def test_weekly_probe_survives_resolution_failure(self):
        with patch("socket.gethostbyname", side_effect=socket.gaierror("nope")), \
             patch.object(ms, "_request", return_value=_resp(200)):
            r = ms.check_domain("nope.example", "shortener", 1.0)
        assert r.verdict == "alive"
