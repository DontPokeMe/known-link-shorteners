"""Fingerprint matching and hosting classification, with no network access."""
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import fingerprint_software as fs
import validate_data as vd

ROOT = Path(__file__).resolve().parent.parent


def resp(status=200, body="", headers=None, cookies=None):
    return fs.Response(status=status, body=body, headers={k.lower(): v for k, v in (headers or {}).items()},
                       cookies=cookies or [])


class FakeSite:
    """Serves canned responses per (domain, path); anything else is a 404."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def __call__(self, domain, path):
        self.calls.append((domain, path))
        if domain in self.pages.get("_down", []):
            return fs.Response(status=None, error="ConnectionError")
        return self.pages.get((domain, path), resp(404, "not found"))


# A complete, schema-valid fingerprint (the validator tests use it).
APP = {
    "software": "demoapp",
    "name": "DemoApp",
    "homepage": "https://demoapp.example/",
    "source_code": "https://git.example/demoapp",
    "managed_domains": ["demo.app"],
    "min_score": 4,
    "checks": [
        {"id": "api-error", "path": "/api.php", "status": [200, 400], "json": {"errorCode": None, "message": "missing"}, "weight": 3},
        {"id": "title", "path": "/", "body": "<title>DemoApp", "weight": 2},
        {"id": "admin-page", "path": "/admin/", "stage": 2, "body": "Powered by DemoApp", "weight": 2},
    ],
}
# Matching-only fixtures: just the fields identify() reads.
HOSTED = {
    "software": "hosted",
    "branded_cname_suffixes": ["cname.hosted.example"],
    "min_score": 3,
    "checks": [
        {"id": "powered", "path": "/", "header": {"name": "X-Powered-By", "pattern": "^Hosted$"}, "weight": 3},
        {"id": "placeholder", "path": "/", "body": "customer domain on Hosted", "weight": 3, "hosting": "branded"},
    ],
}
OTHER = {"software": "otherapp", "min_score": 2, "checks": [{"id": "cookie", "path": "/", "cookie": "^other_session$", "weight": 2}]}


def no_cnames(domain):
    return []


class TestCheckMatching:
    def test_all_conditions_must_hold(self):
        check = {"id": "c", "path": "/", "status": [200], "body": "hello", "header": {"name": "Server", "pattern": "nginx"}}
        assert fs.check_matches(check, resp(200, "Hello world", {"Server": "nginx/1.25"}))
        assert not fs.check_matches(check, resp(500, "Hello world", {"Server": "nginx/1.25"}))
        assert not fs.check_matches(check, resp(200, "Hello world", {"Server": "caddy"}))
        assert not fs.check_matches(check, resp(200, "Hello world"))

    def test_json_keys_and_values(self):
        check = {"id": "j", "path": "/h", "json": {"status": "^pass$", "links.about": None}}
        assert fs.check_matches(check, resp(200, json.dumps({"status": "pass", "links": {"about": "x"}})))
        assert not fs.check_matches(check, resp(200, json.dumps({"status": "fail", "links": {"about": "x"}})))
        assert not fs.check_matches(check, resp(200, json.dumps({"status": "pass"})))
        assert not fs.check_matches(check, resp(200, "<html>not json</html>"))

    def test_no_response_never_matches(self):
        assert not fs.check_matches({"id": "x", "path": "/", "body": ""}, fs.Response(status=None, error="boom"))


class TestIdentify:
    def test_self_hosted_when_checks_reach_min_score(self):
        site = FakeSite({
            ("sho.rt", "/"): resp(200, "<title>DemoApp - shortener</title>"),
            ("sho.rt", "/api.php"): resp(400, json.dumps({"errorCode": 400, "message": "Missing parameters"})),
        })
        r = fs.identify("sho.rt", [APP, OTHER], site, no_cnames)
        assert (r.software, r.hosting) == ("demoapp", "self-hosted")
        assert r.score == 5
        assert r.evidence == ["api-error", "title"]
        assert ("sho.rt", "/admin/") in site.calls  # stage 2 ran after the stage-1 hits

    def test_below_min_score_is_unknown_not_negative(self):
        site = FakeSite({("sho.rt", "/"): resp(200, "<title>DemoApp</title>")})
        r = fs.identify("sho.rt", [APP], site, no_cnames)
        assert r.software is None and r.hosting is None
        assert r.candidates and r.candidates[0]["software"] == "demoapp"

    def test_stage_two_only_runs_after_a_stage_one_hit(self):
        site = FakeSite({("quiet.example", "/"): resp(200, "<html>nothing</html>")})
        fs.identify("quiet.example", [APP], site, no_cnames)
        assert ("quiet.example", "/admin/") not in site.calls
        fs.identify("quiet.example", [APP], site, no_cnames, deep=True)
        assert ("quiet.example", "/admin/") in site.calls

    def test_managed_domain_needs_no_requests(self):
        site = FakeSite({})
        r = fs.identify("demo.app", [APP], site, no_cnames)
        assert (r.software, r.hosting) == ("demoapp", "managed")
        assert site.calls == []

    def test_branded_by_cname(self):
        site = FakeSite({})
        r = fs.identify("go.brand.com", [APP, HOSTED], site, lambda d: ["x.cname.hosted.example"])
        assert (r.software, r.hosting) == ("hosted", "branded")
        assert r.evidence == ["cname:x.cname.hosted.example"]

    def test_cname_suffix_must_match_on_a_label_boundary(self):
        site = FakeSite({("go.brand.com", "/"): resp(200, "")})
        r = fs.identify("go.brand.com", [HOSTED], site, lambda d: ["evilcname.hosted.example.net"])
        assert r.hosting is None

    def test_a_branded_check_marks_the_match_branded(self):
        header_only = FakeSite({("apex.brand.com", "/"): resp(302, "", {"X-Powered-By": "Hosted"})})
        assert fs.identify("apex.brand.com", [HOSTED], header_only, no_cnames).hosting == "self-hosted"
        placeholder = FakeSite({("apex.brand.com", "/"): resp(200, "a customer domain on Hosted")})
        r = fs.identify("apex.brand.com", [HOSTED], placeholder, no_cnames)
        assert (r.software, r.hosting, r.evidence) == ("hosted", "branded", ["placeholder"])

    def test_unreachable_domain_gets_one_request(self):
        site = FakeSite({"_down": ["gone.example"]})
        r = fs.identify("gone.example", [APP, OTHER], site, no_cnames)
        assert not r.reachable and r.error == "ConnectionError"
        assert site.calls == [("gone.example", "/")]

    def test_equal_scores_are_reported_as_ambiguous(self):
        a = {**OTHER, "software": "a-app"}
        b = {**OTHER, "software": "b-app"}
        site = FakeSite({("twin.example", "/"): resp(200, "", cookies=["other_session"])})
        r = fs.identify("twin.example", [a, b], site, no_cnames)
        assert r.software is None
        assert r.ambiguous == ["a-app", "b-app"]

    def test_fetcher_caches_shared_paths(self):
        site = FakeSite({("sho.rt", "/"): resp(200, "<title>DemoApp</title>")})
        fetch = fs.Fetcher(site)
        fs.identify("sho.rt", [APP, OTHER, HOSTED], fetch, no_cnames)
        assert site.calls.count(("sho.rt", "/")) == 1
        assert fetch.requests == len(set(site.calls))

    def test_host_slot_is_held_only_for_the_domains_own_requests(self):
        slot = threading.Semaphore(1)
        held = []

        def slot_free():
            if slot.acquire(blocking=False):
                slot.release()
                return True
            return False

        def resolve(domain):
            held.append(("dns", slot_free()))
            return []

        def site(domain, path):
            held.append(("http", slot_free()))
            return resp(404)

        fs.identify("demo.app", [APP], site, resolve, host_slot=slot)
        assert held == []  # managed: nothing requested
        fs.identify("x.example", [APP, HOSTED], site, resolve, host_slot=slot)
        assert held[0] == ("dns", True)                                  # free during the DNS lookup
        assert [free for kind, free in held if kind == "http"] == [False] * (len(held) - 1)  # taken for HTTP
        assert slot_free()                                               # and released afterwards


class TestTransport:
    def test_dns_failure_skips_the_domain_without_requests(self):
        get = MagicMock()
        with patch("fingerprint_software.socket.gethostbyname", side_effect=OSError("nxdomain")):
            results, made = fs.run_many(["gone.example"], [APP], 2, False, no_cnames, get=get)
        assert not results["gone.example"].reachable
        assert results["gone.example"].error.startswith("dns:")
        assert made == 0 and not get.called

    def test_run_many_counts_requests_across_domains(self):
        site = FakeSite({})
        with patch("fingerprint_software.socket.gethostbyname", return_value="10.0.0.1"):
            results, made = fs.run_many(["a.example", "b.example"], [APP], 2, False, None, get=site)
        assert set(results) == {"a.example", "b.example"}
        assert made == len(site.calls)

    def test_doh_reads_the_whole_cname_chain_from_one_query(self):
        session = MagicMock()
        session.get.return_value.json.return_value = {"Answer": [
            {"type": 5, "data": "go.brand.com.cdn.example."},
            {"type": 5, "data": "cname.dub.co."},
            {"type": 1, "data": "76.76.21.21"},
        ]}
        with patch("fingerprint_software.get_session", return_value=session):
            assert fs.doh_cnames("go.brand.com") == ["go.brand.com.cdn.example", "cname.dub.co"]
        assert session.get.call_count == 1

    def test_http_get_does_not_retry_http_after_a_dns_error(self):
        session = MagicMock()
        session.get.side_effect = fs.requests.exceptions.ConnectionError("Failed to resolve 'gone.example'")
        with patch("fingerprint_software.get_session", return_value=session):
            r = fs.http_get("gone.example", "/")
        assert r.status is None
        assert session.get.call_count == 1


class TestMain:
    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        data = tmp_path / "data"
        data.mkdir()
        (data / "fingerprints.json").write_text(json.dumps([APP, {**OTHER, "reference_instances": ["ref.example"]}]))
        for name in fs.ACTIVE_FILES:
            (data / name).write_text("[]")
        (data / "shorteners.json").write_text(json.dumps([{"domain": "sho.rt"}]))
        monkeypatch.setattr(fs, "DATA", data)
        monkeypatch.setattr(fs, "FINGERPRINTS_FILE", data / "fingerprints.json")
        return data

    def test_write_skips_fingerprints_that_fail_verification(self, repo):
        seen = {}

        def fake_run_many(domains, fingerprints, *args, **kwargs):
            seen["fingerprints"] = [fp["software"] for fp in fingerprints]
            return {"sho.rt": fs.Result(domain="sho.rt", software="demoapp", hosting="self-hosted")}, 3

        with patch.object(fs, "verify", return_value={"otherapp"}), patch.object(fs, "run_many", fake_run_many):
            assert fs.main(["--from-data", "--write", "--no-dns"]) == 0
        assert seen["fingerprints"] == ["demoapp"]
        assert json.loads((repo / "shorteners.json").read_text()) == [
            {"domain": "sho.rt", "software": "demoapp", "hosting": "self-hosted"}]

    def test_domains_are_normalised_and_invalid_ones_dropped(self, repo):
        seen = {}

        def fake_run_many(domains, *args, **kwargs):
            seen["domains"] = domains
            return {}, 0

        with patch.object(fs, "run_many", fake_run_many):
            fs.main(["--domains", "https://Sho.RT/abc", "not a domain", "--no-dns"])
        assert seen["domains"] == ["sho.rt"]

    def test_write_requires_from_data(self, repo):
        with pytest.raises(SystemExit):
            fs.main(["--domains", "sho.rt", "--write"])


class TestApplyResults:
    def test_sets_fields_and_never_clears_on_a_miss(self):
        entries = [
            {"domain": "a.example"},
            {"domain": "b.example", "software": "demoapp", "hosting": "self-hosted"},
        ]
        results = {
            "a.example": fs.Result(domain="a.example", software="demoapp", hosting="self-hosted"),
            "b.example": fs.Result(domain="b.example"),
        }
        assert fs.apply_results(entries, results) == 1
        assert entries[0]["software"] == "demoapp"
        assert entries[1]["software"] == "demoapp"


class TestDataFiles:
    def test_fingerprints_file_is_valid(self):
        path = ROOT / "data" / "fingerprints.json"
        assert vd.validate_fingerprints(json.loads(path.read_text(encoding="utf-8")), path) == []

    def test_validator_catches_bad_fingerprints(self):
        bad = [
            {**APP, "min_score": 99},
            {**APP, "checks": [{"id": "x", "path": "/", "body": "(unclosed"}, {"id": "x", "path": "/", "body": "y"}]},
        ]
        errors = vd.validate_fingerprints(bad, Path("fp.json"))
        assert any("duplicate software id" in e for e in errors)
        assert any("duplicate check id" in e for e in errors)
        assert any("exceeds the total check weight" in e for e in errors)
        assert any("invalid regex" in e for e in errors)

    def test_status_only_checks_are_rejected(self):
        pytest.importorskip("jsonschema")
        weak = [{**APP, "checks": [{"id": "x", "path": "/", "status": [200]}]}]
        assert vd.validate_fingerprints(weak, Path("fp.json"))

    def test_unknown_software_ids_are_rejected(self):
        errors = vd.check_software_ids([{"domain": "a.example", "software": "nope"}], Path("s.json"), {"demoapp"})
        assert errors and "nope" in errors[0]

    def test_hosting_and_software_go_together(self):
        pytest.importorskip("jsonschema")
        base = {"domain": "a.example", "type": "shortener", "status": "active", "added_at": "2026-09-17",
                "source": "internal", "evidence": ["https://a.example/"]}
        path = Path("s.json")
        assert vd.validate_active_schema([{**base, "software": "yourls", "hosting": "self-hosted"}], path) == []
        assert vd.validate_active_schema([{**base, "hosting": "self-hosted"}], path)
        assert vd.validate_active_schema([{**base, "software": "yourls"}], path)
        assert vd.validate_active_schema([{**base, "software": "yourls", "hosting": "cloud"}], path)
