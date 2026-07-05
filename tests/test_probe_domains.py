import socket
import threading
from unittest.mock import MagicMock, patch

import pytest

import probe_domains as pd


def _resp(status, location=None, headers=None):
    m = MagicMock()
    m.status_code = status
    h = {"Location": location} if location else {}
    if headers:
        h.update(headers)
    m.headers = h
    return m


class TestClassification:
    @pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
    def test_redirect_statuses_are_active(self, code):
        with patch("probe_domains.requests.get", return_value=_resp(code, location="https://example.com")):
            r = pd._probe_one("d.example", "shortener")
        assert r.classification == "active"
        assert r.status == code

    def test_200_is_active(self):
        with patch("probe_domains.requests.get", return_value=_resp(200)):
            r = pd._probe_one("d.example", "shortener")
        assert r.classification == "active" and r.status == 200

    @pytest.mark.parametrize("code", [403, 404])
    def test_403_404_are_inactive(self, code):
        with patch("probe_domains.requests.get", return_value=_resp(code)):
            r = pd._probe_one("d.example", "shortener")
        assert r.classification == "inactive" and r.status == code

    def test_5xx_is_review(self):
        with patch("probe_domains.requests.get", return_value=_resp(503)):
            r = pd._probe_one("d.example", "shortener")
        assert r.classification == "review" and r.status == 503

    def test_unexpected_4xx_is_review(self):
        with patch("probe_domains.requests.get", return_value=_resp(400)):
            r = pd._probe_one("d.example", "shortener")
        assert r.classification == "review" and r.status == 400
        assert r.message == "Unexpected status"

    def test_429_persisting_is_retry_later_not_review(self):
        call_count = {"n": 0}

        def always_429(*a, **kw):
            call_count["n"] += 1
            return _resp(429, headers={"Retry-After": "0"})

        with patch("probe_domains.requests.get", side_effect=always_429), patch("probe_domains.time.sleep") as sleep_mock:
            r = pd._probe_one("d.example", "shortener")
        assert r.classification == "retry_later"
        assert r.status == 429
        # https: 2 attempts, http: 2 attempts
        assert call_count["n"] == 4
        assert sleep_mock.called

    def test_429_then_recovers_is_active(self):
        seq = [_resp(429, headers={"Retry-After": "0"}), _resp(200)]
        with patch("probe_domains.requests.get", side_effect=seq), patch("probe_domains.time.sleep"):
            r = pd._probe_one("d.example", "shortener")
        assert r.classification == "active" and r.status == 200

    def test_timeout_after_retries_is_review(self):
        with patch("probe_domains.requests.get", side_effect=pd.requests.exceptions.Timeout()), patch("probe_domains.time.sleep"):
            r = pd._probe_one("d.example", "shortener")
        assert r.classification == "review" and r.status == "timeout"

    def test_dns_error_is_inactive(self):
        err = pd.requests.exceptions.ConnectionError("Failed to resolve: Name or service not known")
        with patch("probe_domains.requests.get", side_effect=err):
            r = pd._probe_one("d.example", "shortener")
        assert r.classification == "inactive" and r.status == "dns_error"

    def test_connect_error_after_retries_is_review(self):
        err = pd.requests.exceptions.ConnectionError("Connection refused")
        with patch("probe_domains.requests.get", side_effect=err), patch("probe_domains.time.sleep"):
            r = pd._probe_one("d.example", "shortener")
        assert r.classification == "review" and r.status == "connect_error"


class TestChunkTable:
    def test_single_chunk_when_small(self):
        chunks = pd.chunk_table(["## Title", "| a | b |"], ["| 1 | 2 |"], ["---"])
        assert len(chunks) == 1
        assert "part 1 of" not in chunks[0]

    def test_splits_when_over_limit(self):
        preamble = ["## Title", "| a | b |", "|---|---|"]
        footer = ["---"]
        rows = [f"| row{i} | {'x' * 100} |" for i in range(2000)]
        chunks = pd.chunk_table(preamble, rows, footer, limit=5000)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= 5000 + 100  # part-marker prefix adds a little overhead
            # every chunk is self-contained: repeats the header
            assert "| a | b |" in c

    def test_real_world_regression_1114_rows(self):
        # The exact shape that broke issue #3039: 1114 rows exceeding GitHub's 65536 limit.
        results = [
            pd.ProbeResult(domain=f"d{i}.example.com", origin="shortener", classification="review",
                           status=301, scheme="https", location=f"https://target-{i}.example.com/path")
            for i in range(1114)
        ]
        chunks = pd.active_review_body(results)
        assert len(chunks) > 1
        for c in chunks:
            assert len(c) <= pd.GITHUB_BODY_LIMIT


class TestSyncReviewIssue:
    def _fake(self, status_code, json_data=None, text=""):
        m = MagicMock()
        m.status_code = status_code
        m.json.return_value = json_data or {}
        m.text = text
        return m

    def test_noop_when_no_results_and_no_existing_issue(self):
        with patch("probe_domains.requests.get") as g, patch("probe_domains.requests.post") as p, patch("probe_domains.requests.patch") as pt:
            g.return_value = self._fake(200, [])
            action, err = pd.sync_review_issue("Title", [], pd.active_review_body, "2026-07-05", "o/r", "tok")
        assert action == "noop" and err is None
        assert p.call_count == 0 and pt.call_count == 0

    def test_closes_existing_issue_when_clear(self):
        with patch("probe_domains.requests.get") as g, patch("probe_domains.requests.post") as p, patch("probe_domains.requests.patch") as pt:
            g.return_value = self._fake(200, [{"number": 42, "title": "Title"}])
            p.return_value = self._fake(201)
            pt.return_value = self._fake(200)
            action, err = pd.sync_review_issue("Title", [], pd.active_review_body, "2026-07-05", "o/r", "tok")
        assert action == "closed" and err is None
        assert "/issues/42/comments" in p.call_args[0][0]
        assert "/issues/42" in pt.call_args[0][0]

    def test_creates_new_issue_when_results_and_none_exists(self):
        r1 = pd.ProbeResult(domain="example.com", origin="shortener", classification="review", status=301, scheme="https", location=None)
        with patch("probe_domains.requests.get") as g, patch("probe_domains.requests.post") as p:
            g.return_value = self._fake(200, [])
            p.return_value = self._fake(201, {"number": 5})
            action, err = pd.sync_review_issue("Title", [r1], pd.active_review_body, "2026-07-05", "o/r", "tok")
        assert action == "created" and err is None
        assert p.call_count == 1
        assert p.call_args[1]["json"]["title"] == "Title"

    def test_comments_on_existing_issue_instead_of_creating(self):
        r1 = pd.ProbeResult(domain="example.com", origin="shortener", classification="review", status=301, scheme="https", location=None)
        with patch("probe_domains.requests.get") as g, patch("probe_domains.requests.post") as p:
            g.return_value = self._fake(200, [{"number": 7, "title": "Title"}])
            p.return_value = self._fake(201)
            action, err = pd.sync_review_issue("Title", [r1], pd.active_review_body, "2026-07-05", "o/r", "tok")
        assert action == "commented" and err is None
        assert "/issues/7/comments" in p.call_args[0][0]

    def test_multi_chunk_create_posts_all_chunks(self):
        results = [
            pd.ProbeResult(domain=f"d{i}.example.com", origin="shortener", classification="review",
                           status=301, scheme="https", location=f"https://target-{i}.example.com")
            for i in range(1114)
        ]
        chunks = pd.active_review_body(results)
        assert len(chunks) > 1
        with patch("probe_domains.requests.get") as g, patch("probe_domains.requests.post") as p:
            g.return_value = self._fake(200, [])
            responses = [self._fake(201, {"number": 99})] + [self._fake(201) for _ in range(len(chunks) - 1)]
            p.side_effect = responses
            action, err = pd.sync_review_issue(pd.ACTIVE_REVIEW_TITLE, results, pd.active_review_body, "2026-07-05", "o/r", "tok")
        assert action == "created" and err is None
        assert p.call_count == len(chunks)
        assert p.call_args_list[0][0][0].endswith("/issues")
        for call in p.call_args_list[1:]:
            assert "/issues/99/comments" in call[0][0]


class TestReviewHistory:
    def _review_result(self, domain="d.example", status="timeout"):
        return pd.ProbeResult(domain=domain, origin="shortener", classification="review", status=status, scheme="https", location=None)

    def test_first_month_creates_history_entry(self):
        new_history, still, demoted = pd.update_review_history({}, [self._review_result()], "2026-01-01")
        assert len(still) == 1 and len(demoted) == 0
        assert new_history[0]["consecutive_review_count"] == 1
        assert new_history[0]["first_flagged_at"] == "2026-01-01"

    def test_demotes_at_threshold(self):
        history = {}
        for month in range(1, pd.REPEAT_OFFENDER_THRESHOLD):
            new_history, still, demoted = pd.update_review_history(history, [self._review_result()], f"2026-0{month}-01")
            assert len(demoted) == 0
            history = {e["domain"]: e for e in new_history}
        # the threshold-th consecutive month demotes instead of continuing to flag
        new_history, still, demoted = pd.update_review_history(history, [self._review_result()], "2026-12-01")
        assert len(demoted) == 1
        assert len(still) == 0
        assert new_history == []  # streak resolved, dropped from history

    def test_recovery_resets_streak(self):
        history = {"d.example": {"domain": "d.example", "origin": "shortener", "consecutive_review_count": 2,
                                   "first_flagged_at": "2026-01-01", "last_status": "timeout", "last_checked_at": "2026-02-01"}}
        # domain recovered -- not present in this run's active_review at all
        new_history, still, demoted = pd.update_review_history(history, [], "2026-03-01")
        assert new_history == [] and still == [] and demoted == []

    def test_unrelated_domain_does_not_affect_others_streak(self):
        history = {"other.example": {"domain": "other.example", "origin": "shortener", "consecutive_review_count": 2,
                                       "first_flagged_at": "2026-01-01", "last_status": "timeout", "last_checked_at": "2026-02-01"}}
        new_history, still, demoted = pd.update_review_history(history, [self._review_result("new.example")], "2026-03-01")
        # "other.example" recovered (absent from this run) and is dropped; "new.example" is new at count 1
        domains = {e["domain"] for e in new_history}
        assert domains == {"new.example"}


class TestPerHostThrottling:
    @pytest.fixture(autouse=True)
    def _clear_semaphores(self):
        pd._host_semaphores.clear()
        yield
        pd._host_semaphores.clear()

    def test_domains_resolving_to_same_ip_share_a_semaphore(self):
        with patch("probe_domains.socket.gethostbyname", return_value="1.2.3.4"):
            sem_a = pd._get_host_semaphore("a.example")
            sem_b = pd._get_host_semaphore("b.example")
        assert sem_a is sem_b

    def test_domains_resolving_to_different_ips_get_different_semaphores(self):
        def fake_resolve(domain):
            return "1.2.3.4" if domain == "a.example" else "5.6.7.8"

        with patch("probe_domains.socket.gethostbyname", side_effect=fake_resolve):
            sem_a = pd._get_host_semaphore("a.example")
            sem_b = pd._get_host_semaphore("b.example")
        assert sem_a is not sem_b

    def test_resolution_failure_falls_back_to_per_domain_and_does_not_raise(self):
        with patch("probe_domains.socket.gethostbyname", side_effect=socket.gaierror("nope")):
            sem = pd._get_host_semaphore("unresolvable.example")
        assert isinstance(sem, threading.Semaphore)

    def test_probe_one_still_works_when_resolution_fails(self):
        with patch("probe_domains.socket.gethostbyname", side_effect=socket.gaierror("nope")), \
             patch("probe_domains.requests.get", return_value=_resp(200)):
            r = pd.probe_one("unresolvable.example", "shortener")
        assert r.classification == "active" and r.status == 200
