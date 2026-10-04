"""Candidate triage is nearly the whole weekly runtime. These pin what keeps it cheap."""
import json
import sys

import maintain_shorteners as ms
from maintain_shorteners import CheckResult

MODELS = ["qwen2.5:7b", "llama3.1:8b"]


def _stub_classify(calls):
    def classify(domain, host, model, timeout):
        calls.append((domain, model))
        category = "shortener" if domain.startswith("live") else "other"
        return {"domain": domain, "category": category, "confidence": 0.9, "reason": "stub"}
    return classify


def _stub_liveness(targets, workers, timeout):
    return [
        CheckResult(d, o, "dead", "dns_error", "https", "HEAD", "")
        if d.startswith("dead") else CheckResult(d, o, "alive", 301, "https", "HEAD", "")
        for d, o in targets
    ]


class TestRunTriage:
    def test_models_run_one_after_another_not_interleaved(self, monkeypatch):
        # Two models generating at once on a CPU runner split the same cores.
        calls = []
        monkeypatch.setattr(ms, "classify_candidate", _stub_classify(calls))
        domains = [f"d{i}.example" for i in range(6)]

        ms.run_triage(domains, "http://ollama", MODELS, workers=2, timeout=1)

        order = [model for _, model in calls]
        assert order == [MODELS[0]] * 6 + [MODELS[1]] * 6

    def test_every_model_still_votes_on_every_domain(self, monkeypatch):
        monkeypatch.setattr(ms, "classify_candidate", _stub_classify([]))
        votes = ms.run_triage(["a.example", "b.example"], "http://ollama", MODELS, 2, 1)
        for vs in votes.values():
            assert sorted(v["model"] for v in vs) == sorted(MODELS)


class TestDeadCandidatesSkipTriage:
    def test_dead_candidate_is_rejected_without_asking_any_model(self, monkeypatch, tmp_path):
        calls = []
        monkeypatch.setattr(ms, "classify_candidate", _stub_classify(calls))
        monkeypatch.setattr(ms, "run_liveness", _stub_liveness)
        monkeypatch.setattr(ms, "select_models", lambda *a, **k: list(MODELS))
        cand = tmp_path / "candidates.txt"
        cand.write_text(
            "deadlink.example  # src=https://a\n"
            "liveshort.example  # src=https://a\n"
            "notshort.example  # src=https://a\n"
        )
        report = tmp_path / "report.json"
        monkeypatch.setattr(sys, "argv", [
            "maintain_shorteners.py", "--no-check", "--dry-run", "--no-flat",
            "--candidates", str(cand), "--rejected", str(tmp_path / "rejected.txt"),
            "--review", str(tmp_path / "review.txt"), "--report", str(report),
        ])

        assert ms.main() == 0

        assert {d for d, _ in calls} == {"liveshort.example", "notshort.example"}
        ingest = json.loads(report.read_text())["ingest"]
        assert ingest["accepted"] == ["liveshort.example"]
        assert set(ingest["rejected"]) == {"deadlink.example", "notshort.example"}
        dead = next(d for d in ingest["decisions"] if d["domain"] == "deadlink.example")
        assert "dead" in dead["reason"]
