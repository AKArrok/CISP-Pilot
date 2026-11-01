import pytest
from sqlalchemy import create_engine

from http_auth_helpers import authenticated_http, isolated_database


@pytest.fixture()
def correction_db(tmp_path, monkeypatch):
    from storage import db
    from storage.models import Base, KnowledgePoint, QuestionAttribution

    db_path = tmp_path / "correction.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    with isolated_database(engine, ready=True):
        with db.session() as session:
            session.add_all(
                [
                    KnowledgePoint(id="kp-a", domain="domain-a", name="A", status="active"),
                    KnowledgePoint(id="kp-b", domain="domain-b", name="B", status="active"),
                    KnowledgePoint(id="kp-c", domain="domain-c", name="C", status="candidate"),
                ]
            )
            session.add_all(
                [
                    QuestionAttribution(
                        question_id="probable-q",
                        candidate_point_id="kp-a",
                        candidate_domain="domain-a",
                        attribution_status="probable",
                        agreement_count=1,
                        evidence_json="{}",
                        reason="candidate",
                        classifier_version="test",
                        created_at=1,
                        updated_at=2,
                    ),
                    QuestionAttribution(
                        question_id="confirmed-q",
                        candidate_point_id="kp-a",
                        candidate_domain="domain-a",
                        attribution_status="confirmed",
                        agreement_count=3,
                        evidence_json="{}",
                        reason="confirmed",
                        classifier_version="test",
                        created_at=1,
                        updated_at=1,
                    ),
                ]
            )
            session.commit()

        questions = [
            {
                "id": "probable-q",
                "source": "test",
                "domain": "domain-a",
                "stem": "probable",
                "options": {"A": "wrong", "B": "right"},
                "answer": "B",
                "analysis": "x",
            },
            {
                "id": "confirmed-q",
                "source": "test",
                "domain": "domain-a",
                "stem": "confirmed",
                "options": {"A": "wrong", "B": "right"},
                "answer": "B",
                "analysis": "x",
            },
        ]

        class FakeBank:
            def get_by_id(self, question_id):
                return next((q for q in questions if q["id"] == question_id), None)

            def all(self, domain=None):
                return [q for q in questions if domain is None or q["domain"] == domain]

            def available(self, domain=None, *, thread_id="default"):
                return self.all(domain)

        import quiz

        monkeypatch.setattr(quiz.QuestionBank, "get", classmethod(lambda cls: FakeBank()))
        monkeypatch.setattr("quiz.grader.related_chunks", lambda question: [])
        from server import app

        yield authenticated_http(app)
    engine.dispose()


def test_user_correction_is_scoped_and_replaces_pending_calibration(correction_db):
    import quiz
    from storage import repos

    thread = correction_db.own_thread("learner-a")
    quiz.submit_answer("probable-q", "A", thread_id=thread)
    original = repos.pending_calibrations(thread)
    assert len(original) == 1
    assert original[0]["knowledge_point_id"] == "kp-a"

    response = correction_db.client.post(
        "/api/attributions/probable-q/correction",
        json={
            "thread_id": thread,
            "corrected_point_id": "kp-b",
            "reason": "user says the item tests B",
        },
    )
    assert response.status_code == 200
    assert response.json()["retracted_calibrations"] == 1
    assert response.json()["replacement_calibration_id"] is not None

    effective = repos.effective_question_attribution(thread, "probable-q")
    assert effective["candidate_point_id"] == "kp-b"
    assert effective["source"] == "user_correction"
    assert (
        repos.effective_question_attribution("learner-b", "probable-q")["candidate_point_id"]
        == "kp-a"
    )

    pending = repos.pending_calibrations(thread)
    assert len(pending) == 1
    assert pending[0]["knowledge_point_id"] == "kp-b"
    stats = (
        correction_db.client
        .get(
            "/api/stats",
            params={"thread_id": thread},
        )
        .json()
    )
    visible = next(row for row in stats["attributions"] if row["question_id"] == "probable-q")
    assert visible["candidate_point_id"] == "kp-b"
    assert visible["source"] == "user_correction"
    channels = stats["attribution_evidence_channels"]
    assert channels["user_corrections"] == {
        "events": 1,
        "profile_replays": 0,
        "calibration_replacements": 1,
        "calibrations_retracted": 1,
    }
    assert channels["calibration_outcomes"] == {
        "pending": 1,
        "confirmed": 0,
        "retracted": 1,
        "inconclusive": 0,
    }


def test_correction_reuses_existing_pending_for_target_point(correction_db):
    import quiz
    from storage import repos

    thread = correction_db.own_thread("learner-replacement-reuse")
    existing = repos.create_calibration_hypothesis(
        thread,
        "kp-b",
        "other-trigger",
        False,
        "existing target calibration",
    )
    quiz.submit_answer("probable-q", "A", thread_id=thread)

    response = correction_db.client.post(
        "/api/attributions/probable-q/correction",
        json={"thread_id": thread, "corrected_point_id": "kp-b"},
    )

    assert response.status_code == 200
    assert response.json()["replacement_calibration_id"] == existing["id"]
    pending = repos.pending_calibrations(thread)
    assert [(item["id"], item["knowledge_point_id"]) for item in pending] == [
        (existing["id"], "kp-b"),
    ]
    assert repos.calibration_status_counts(thread) == {
        "pending": 1,
        "confirmed": 0,
        "retracted": 1,
        "inconclusive": 0,
    }


def test_applied_correction_replays_exact_evidence_and_is_reversible(correction_db):
    import quiz
    from storage import repos

    thread = correction_db.own_thread("learner-applied")
    quiz.submit_answer("confirmed-q", "A", thread_id=thread)
    before = repos.learner_knowledge_states(thread)
    assert [(row["knowledge_point_id"], row["attempts"], row["correct"]) for row in before] == [
        ("kp-a", 1, 0)
    ]

    client = correction_db.client
    response = client.post(
        "/api/attributions/confirmed-q/correction",
        json={"thread_id": thread, "corrected_point_id": "kp-b"},
    )
    assert response.status_code == 200
    assert response.json()["replayed_attempts"] == 1
    assert response.json()["replacement_calibration_id"] is None
    moved = repos.learner_knowledge_states(thread)
    assert [(row["knowledge_point_id"], row["attempts"], row["correct"]) for row in moved] == [
        ("kp-b", 1, 0)
    ]
    assert repos.attempt_rows(thread)[0][0] == "domain-b"

    response = client.post(
        "/api/attributions/confirmed-q/correction",
        json={"thread_id": thread, "corrected_point_id": "kp-a"},
    )
    assert response.status_code == 200
    assert response.json()["previous_point_id"] == "kp-b"
    restored = repos.learner_knowledge_states(thread)
    assert [(row["knowledge_point_id"], row["attempts"], row["correct"]) for row in restored] == [
        ("kp-a", 1, 0)
    ]
    corrections = repos.attribution_corrections(thread)
    assert len(corrections) == 2
    assert all(row["replayed_attempts"] == 1 for row in corrections)
    channels = client.get(
        "/api/stats",
        params={"thread_id": thread},
    ).json()["attribution_evidence_channels"]["user_corrections"]
    assert channels == {
        "events": 2,
        "profile_replays": 2,
        "calibration_replacements": 0,
        "calibrations_retracted": 0,
    }


def test_correction_to_current_base_target_is_noop(correction_db):
    import quiz
    from storage import repos

    thread = correction_db.own_thread("learner-correction-noop")
    quiz.submit_answer("confirmed-q", "A", thread_id=thread)
    response = correction_db.client.post(
        "/api/attributions/confirmed-q/correction",
        json={
            "thread_id": thread,
            "corrected_point_id": "kp-a",
            "reason": "same as current attribution",
        },
    )

    assert response.status_code == 200
    result = response.json()
    assert result["deduplicated"] is True
    assert result["id"] is None
    assert result["replayed_attempts"] == 0
    assert repos.attribution_corrections(thread) == []
    state = repos.learner_knowledge_states(thread)
    assert [(row["knowledge_point_id"], row["attempts"]) for row in state] == [
        ("kp-a", 1),
    ]


def test_same_target_correction_retry_is_idempotent(correction_db):
    import quiz
    from storage import repos

    thread = correction_db.own_thread("learner-correction-retry")
    quiz.submit_answer("confirmed-q", "A", thread_id=thread)
    client = correction_db.client
    first = client.post(
        "/api/attributions/confirmed-q/correction",
        json={
            "thread_id": thread,
            "corrected_point_id": "kp-b",
            "reason": "first correction",
        },
    )
    retry = client.post(
        "/api/attributions/confirmed-q/correction",
        json={
            "thread_id": thread,
            "corrected_point_id": "kp-b",
            "reason": "transport retry must not create another event",
        },
    )

    assert first.status_code == retry.status_code == 200
    assert first.json()["replayed_attempts"] == 1
    assert retry.json()["deduplicated"] is True
    assert retry.json()["replayed_attempts"] == 0
    assert retry.json()["retracted_calibrations"] == 0
    assert retry.json()["replacement_calibration_id"] is None
    corrections = repos.attribution_corrections(thread)
    assert len(corrections) == 1
    state = repos.learner_knowledge_states(thread)
    assert [(row["knowledge_point_id"], row["attempts"]) for row in state] == [
        ("kp-b", 1),
    ]


def test_legacy_applied_evidence_without_point_ledger_is_rejected(correction_db):
    from quiz import store
    from storage import repos

    thread = correction_db.own_thread("learner-legacy")
    correction_db.own_thread("other")
    store.record_attempt(
        thread,
        "confirmed-q",
        "test",
        "domain-a",
        "confirmed",
        "A",
        False,
        attribution_status="confirmed",
        profile_applied=True,
    )
    repos.update_learner_knowledge_states(thread, ["kp-a"], False)
    client = correction_db.client
    stats = client.get("/api/stats", params={"thread_id": thread})
    assert stats.status_code == 200
    assert stats.json()["legacy_applied_attempts_without_point"] == 1
    review = client.get("/api/attribution-review", params={"thread_id": thread})
    assert review.status_code == 200
    assert next(
        item for item in review.json()["items"] if item["question_id"] == "confirmed-q"
    )["legacy_applied_attempts_without_point"] == 1
    other_thread = client.get("/api/attribution-review", params={"thread_id": "other"})
    assert client.get("/api/stats", params={"thread_id": "other"}).json()[
        "legacy_applied_attempts_without_point"
    ] == 0
    assert all(
        item["legacy_applied_attempts_without_point"] == 0
        for item in other_thread.json()["items"]
    )

    response = client.post(
        "/api/attributions/confirmed-q/correction",
        json={"thread_id": thread, "corrected_point_id": "kp-b"},
    )
    assert response.status_code == 409
    assert repos.attribution_corrections(thread) == []


def test_active_knowledge_point_endpoint_excludes_candidates(correction_db):

    response = correction_db.client.get(
        "/api/knowledge-points",
        params={"status": "active"},
    )
    assert response.status_code == 200
    assert [row["id"] for row in response.json()] == ["kp-a", "kp-b"]
    assert (
        correction_db.client
        .get(
            "/api/knowledge-points",
            params={"status": "invalid"},
        )
        .status_code
        == 422
    )


def test_attribution_status_filter_runs_before_limit(correction_db):

    response = correction_db.client.get(
        "/api/attributions",
        params={"status": "confirmed", "limit": 1},
    )
    assert response.status_code == 200
    assert [row["question_id"] for row in response.json()] == ["confirmed-q"]


def test_attribution_review_cursor_pages_overlay_only_current_thread(correction_db):
    from storage import db
    from storage.models import QuestionAttributionCorrection

    correction_db.own_thread("review-thread-a")
    correction_db.own_thread("review-thread-b")

    with db.session() as session:
        session.add_all([
            QuestionAttributionCorrection(
                thread_id="review-thread-a",
                question_id="probable-q",
                corrected_point_id="kp-b",
                corrected_domain="domain-b",
                previous_point_id="kp-a",
                previous_status="probable",
                reason="reviewed correction",
                replayed_attempts=0,
                retracted_calibrations=0,
                replacement_calibration_id=None,
                created_at=10,
            ),
            QuestionAttributionCorrection(
                thread_id="review-thread-b",
                question_id="confirmed-q",
                corrected_point_id="kp-b",
                corrected_domain="domain-b",
                previous_point_id="kp-a",
                previous_status="confirmed",
                reason="private to another thread",
                replayed_attempts=0,
                retracted_calibrations=0,
                replacement_calibration_id=None,
                created_at=11,
            ),
        ])
        session.commit()

    client = correction_db.client
    first = client.get(
        "/api/attribution-review",
        params={"thread_id": "review-thread-a", "limit": 1},
    )
    assert first.status_code == 200
    first_page = first.json()
    assert first_page["total"] == 2
    assert first_page["has_more"] is True
    assert first_page["next_cursor"] == "confirmed-q"
    assert first_page["items"][0]["question_id"] == "confirmed-q"
    assert first_page["items"][0]["candidate_point_id"] == "kp-a"
    assert first_page["items"][0]["candidate_point_name"] == "A"
    assert first_page["items"][0]["question_stem"] == "confirmed"
    assert first_page["items"][0]["question_options"] == {"A": "wrong", "B": "right"}
    assert "answer" not in first_page["items"][0]
    assert first_page["items"][0].get("source") is None

    second = client.get(
        "/api/attribution-review",
        params={
            "thread_id": "review-thread-a",
            "limit": 1,
            "after_question_id": first_page["next_cursor"],
        },
    )
    assert second.status_code == 200
    second_page = second.json()
    assert second_page["total"] == 2
    assert second_page["has_more"] is False
    assert second_page["next_cursor"] is None
    assert second_page["items"][0]["question_id"] == "probable-q"
    assert second_page["items"][0]["candidate_point_id"] == "kp-b"
    assert second_page["items"][0]["candidate_point_name"] == "B"
    assert second_page["items"][0]["question_stem"] == "probable"
    assert second_page["items"][0]["source"] == "user_correction"

    filtered = client.get(
        "/api/attribution-review",
        params={"thread_id": "review-thread-a", "question_id": "probable"},
    )
    assert filtered.status_code == 200
    assert [item["question_id"] for item in filtered.json()["items"]] == ["probable-q"]


def test_correction_audit_api_is_thread_scoped_and_cursor_paginated(correction_db):
    from storage import db
    from storage.models import QuestionAttributionCorrection

    correction_db.own_thread("audit-a")
    correction_db.own_thread("audit-b")

    with db.session() as session:
        for index in range(3):
            session.add(
                QuestionAttributionCorrection(
                    thread_id="audit-a",
                    question_id=f"audit-q-{index}",
                    corrected_point_id="kp-b",
                    corrected_domain="domain-b",
                    previous_point_id="kp-a",
                    previous_status="confirmed",
                    reason=f"audit event {index}",
                    replayed_attempts=index,
                    retracted_calibrations=0,
                    replacement_calibration_id=None,
                    created_at=100.0 + index,
                )
            )
        session.add(
            QuestionAttributionCorrection(
                thread_id="audit-b",
                question_id="private-q",
                corrected_point_id="kp-a",
                corrected_domain="domain-a",
                previous_point_id="kp-b",
                previous_status="confirmed",
                reason="other thread",
                replayed_attempts=0,
                retracted_calibrations=0,
                replacement_calibration_id=None,
                created_at=200.0,
            )
        )
        session.commit()

    client = correction_db.client
    first = client.get(
        "/api/attribution-corrections",
        params={"thread_id": "audit-a", "limit": 2},
    )
    assert first.status_code == 200
    first_rows = first.json()
    assert [row["question_id"] for row in first_rows] == ["audit-q-2", "audit-q-1"]
    assert all(row["thread_id"] == "audit-a" for row in first_rows)

    second = client.get(
        "/api/attribution-corrections",
        params={
            "thread_id": "audit-a",
            "limit": 2,
            "before_id": first_rows[-1]["id"],
        },
    )
    assert second.status_code == 200
    second_rows = second.json()
    assert [row["question_id"] for row in second_rows] == ["audit-q-0"]
    assert {row["id"] for row in first_rows}.isdisjoint(row["id"] for row in second_rows)
    assert "private-q" not in {row["question_id"] for row in first_rows + second_rows}

    assert (
        client.get(
            "/api/attribution-corrections",
            params={"thread_id": "", "limit": 2},
        ).status_code
        == 422
    )


def test_profile_summary_keeps_latest_correction_beyond_recent_500(correction_db):
    from storage import db
    from storage.models import QuestionAttributionCorrection

    thread = correction_db.own_thread("correction-over-500")
    with db.session() as session:
        session.add(
            QuestionAttributionCorrection(
                thread_id=thread,
                question_id="probable-q",
                corrected_point_id="kp-a",
                corrected_domain="domain-a",
                previous_point_id=None,
                previous_status="probable",
                reason="first correction superseded later",
                replayed_attempts=0,
                retracted_calibrations=0,
                replacement_calibration_id=None,
                created_at=0,
            )
        )
        session.commit()
    with db.session() as session:
        session.add(
            QuestionAttributionCorrection(
                thread_id=thread,
                question_id="probable-q",
                corrected_point_id="kp-b",
                corrected_domain="domain-b",
                previous_point_id="kp-a",
                previous_status="probable",
                reason="older but still latest for this question",
                replayed_attempts=0,
                retracted_calibrations=0,
                replacement_calibration_id=None,
                created_at=1,
            )
        )
        session.commit()
    with db.session() as session:
        session.add_all(
            QuestionAttributionCorrection(
                thread_id=thread,
                question_id=f"unrelated-q-{index}",
                corrected_point_id="kp-a",
                corrected_domain="domain-a",
                previous_point_id=None,
                previous_status="unverified",
                reason="more recent audit event",
                replayed_attempts=0,
                retracted_calibrations=0,
                replacement_calibration_id=None,
                created_at=2 + index,
            )
            for index in range(500)
        )
        session.commit()

    response = correction_db.client.get("/api/stats", params={"thread_id": thread})
    assert response.status_code == 200
    visible = next(
        row for row in response.json()["attributions"]
        if row["question_id"] == "probable-q"
    )
    assert visible["candidate_point_id"] == "kp-b"
    assert visible["source"] == "user_correction"


def test_correction_observability_is_scoped_windowed_and_not_accuracy(correction_db):
    from storage import db
    from storage.models import QuestionAttributionCorrection

    correction_db.own_thread("observe-a")
    correction_db.own_thread("observe-b")

    rows = [
        QuestionAttributionCorrection(
            thread_id="observe-a",
            question_id="q-1",
            corrected_point_id="kp-b",
            corrected_domain="domain-b",
            previous_point_id="kp-a",
            previous_status="probable",
            reason="human evidence",
            replayed_attempts=0,
            retracted_calibrations=1,
            replacement_calibration_id=7,
            created_at=100.0,
        ),
        QuestionAttributionCorrection(
            thread_id="observe-a",
            question_id="q-1",
            corrected_point_id="kp-a",
            corrected_domain="domain-a",
            previous_point_id="kp-b",
            previous_status="confirmed",
            reason="   ",
            replayed_attempts=2,
            retracted_calibrations=0,
            replacement_calibration_id=None,
            created_at=200.0,
        ),
        QuestionAttributionCorrection(
            thread_id="observe-a",
            question_id="q-2",
            corrected_point_id="kp-b",
            corrected_domain="domain-b",
            previous_point_id=None,
            previous_status="unverified",
            reason="",
            replayed_attempts=1,
            retracted_calibrations=0,
            replacement_calibration_id=None,
            created_at=300.0,
        ),
        QuestionAttributionCorrection(
            thread_id="observe-b",
            question_id="private-q",
            corrected_point_id="kp-a",
            corrected_domain="private-domain",
            previous_point_id="kp-b",
            previous_status="confirmed",
            reason="must not leak",
            replayed_attempts=9,
            retracted_calibrations=9,
            replacement_calibration_id=None,
            created_at=400.0,
        ),
    ]
    with db.session() as session:
        session.add_all(rows)
        session.commit()
        latest_visible_id = rows[2].id

    client = correction_db.client
    response = client.get(
        "/api/attribution-observability",
        params={"thread_id": "observe-a"},
    )
    assert response.status_code == 200
    result = response.json()
    assert result == {
        "metric_semantics": "operational_feedback_not_accuracy",
        "not_accuracy_metric": True,
        "window": {"since": None, "latest_event_id": latest_visible_id},
        "events": 3,
        "unique_questions": 2,
        "repeat_events": 1,
        "reason_provided_events": 1,
        "profile_replays": 2,
        "calibration_replacements": 1,
        "calibrations_retracted": 1,
        "by_target_domain": {"domain-a": 1, "domain-b": 2},
        "by_previous_status": {
            "confirmed": 1,
            "probable": 1,
            "unverified": 1,
        },
        "ledger_integrity": {
            "snapshot_scope": "current_thread_not_event_window",
            "confirmed_calibrations": 0,
            "confirmed_missing_applied_evidence": 0,
            "confirmed_duplicate_applied_evidence": 0,
            "confirmed_unexpected_applied_evidence": 0,
            "confirmed_mismatched_applied_attempts": 0,
            "nonconfirmed_applied_attempts": 0,
            "orphan_applied_attempts": 0,
            "anomalies_total": 0,
            "status": "ok",
        },
    }
    assert "private-domain" not in result["by_target_domain"]

    windowed = client.get(
        "/api/attribution-observability",
        params={"thread_id": "observe-a", "since": 150},
    )
    assert windowed.status_code == 200
    windowed_result = windowed.json()
    assert windowed_result["events"] == 2
    assert windowed_result["unique_questions"] == 2
    assert windowed_result["repeat_events"] == 0
    assert windowed_result["reason_provided_events"] == 0
    assert windowed_result["by_target_domain"] == {"domain-a": 1, "domain-b": 1}
    assert windowed_result["ledger_integrity"] == result["ledger_integrity"]

    empty = client.get(
        "/api/attribution-observability",
        params={"thread_id": "observe-a", "since": 500},
    ).json()
    assert empty["window"] == {"since": 500.0, "latest_event_id": None}
    assert empty["events"] == empty["unique_questions"] == 0
    assert empty["by_target_domain"] == {}
    assert empty["by_previous_status"] == {}
    assert empty["ledger_integrity"] == result["ledger_integrity"]

    assert (
        client.get(
            "/api/attribution-observability",
            params={"thread_id": ""},
        ).status_code
        == 422
    )
    assert (
        client.get(
            "/api/attribution-observability",
            params={"thread_id": "observe-a", "since": -1},
        ).status_code
        == 422
    )


def test_observability_reports_thread_scoped_calibration_ledger_anomalies(
    correction_db,
):
    from storage import db
    from storage.models import CalibrationHypothesis, LearnerAttempt

    thread = correction_db.own_thread("observe-integrity")
    with db.session() as session:
        confirmed = CalibrationHypothesis(
            thread_id=thread,
            knowledge_point_id="kp-a",
            trigger_question_id="trigger",
            trigger_correct=False,
            status="confirmed",
            evidence_json=(
                '[{"question_id":"q-1","correct":false},{"question_id":"q-2","correct":false},{"question_id":"q-mismatch-id","correct":false,"attempt_id":900000},{"question_id":"q-mismatch-answer","correct":false,"attempt_id":900002},{"question_id":"q-legacy-mismatch","correct":false}]'
            ),
            calibration_reason="test",
            presentation_count=2,
            created_at=1,
            updated_at=2,
        )
        pending = CalibrationHypothesis(
            thread_id=thread,
            knowledge_point_id="kp-a",
            trigger_question_id="pending-trigger",
            trigger_correct=False,
            status="pending",
            evidence_json='[{"question_id":"p-1","correct":false}]',
            calibration_reason="test",
            presentation_count=1,
            created_at=1,
            updated_at=2,
        )
        session.add_all([confirmed, pending])
        session.flush()

        def attempt(calibration_id, question_id, applied, *, owner=thread, correct_value=False, attempt_id=None):
            return LearnerAttempt(
                id=attempt_id,
                thread_id=owner,
                question_id=question_id,
                question_source="test",
                domain="domain-a",
                stem=question_id,
                choice="A",
                correct=correct_value,
                answered_at=10,
                attribution_status="confirmed",
                profile_applied=applied,
                calibration_id=calibration_id,
                attribution_point_id="kp-a",
            )

        session.add_all(
            [
                attempt(confirmed.id, "q-1", True),
                attempt(confirmed.id, "q-1", True),
                attempt(confirmed.id, "q-2", False),
                attempt(confirmed.id, "q-extra", True),
                attempt(confirmed.id, "q-mismatch-id", True, correct_value=False, attempt_id=900001),
                attempt(confirmed.id, "q-mismatch-answer", True, correct_value=True, attempt_id=900002),
                attempt(confirmed.id, "q-legacy-mismatch", True, correct_value=True, attempt_id=900003),
                attempt(pending.id, "p-1", True),
                attempt(999, "orphan", True),
                attempt(998, "private-orphan", True, owner="observe-private"),
            ]
        )
        session.commit()

    client = correction_db.client
    result = client.get(
        "/api/attribution-observability",
        params={"thread_id": thread, "since": 999},
    ).json()

    assert result["events"] == 0
    assert result["ledger_integrity"] == {
        "snapshot_scope": "current_thread_not_event_window",
        "confirmed_calibrations": 1,
        "confirmed_missing_applied_evidence": 1,
        "confirmed_duplicate_applied_evidence": 1,
        "confirmed_unexpected_applied_evidence": 1,
        "confirmed_mismatched_applied_attempts": 3,
        "nonconfirmed_applied_attempts": 1,
        "orphan_applied_attempts": 1,
        "anomalies_total": 8,
        "status": "attention",
    }
