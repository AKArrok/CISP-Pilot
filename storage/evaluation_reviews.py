"""Answer/rubric-bound review queue with expiring claims and append-only decisions."""
import time
from typing import Literal
from uuid import uuid4

from pydantic import Field

from storage import evaluation_store as store, evaluation_workspace as workspace


class AttemptReview(store.Review):
    rubric_id: str


class QueueDecision(AttemptReview):
    lock_token: str = Field(min_length=1, max_length=100)
    action: Literal["review", "adjudicate"] = "review"


def _bound(identifier, case_id, attempt, review):
    detail = workspace.detail(identifier, case_id, attempt)
    if (detail["result"] is None or detail["result"]["answer_sha256"] != review.answer_sha256
            or detail["rubric_id"] != review.rubric_id or not review.reason.strip()):
        raise ValueError("Review must bind the actual answer and rubric")
    return detail


def _append(connection, identifier, case_id, attempt, review, actor, queue_id=None):
    payload = store.canonical(review.model_dump(exclude={"lock_token"}))
    row = connection.execute("""SELECT * FROM eval_attempt_reviews WHERE run_id=? AND case_id=?
        AND attempt_no=? AND actor=? AND request_id=?""", (identifier, case_id, attempt, actor, review.request_id)).fetchone()
    if row:
        if row["payload"] != payload or row["queue_id"] != queue_id:
            raise ValueError("Review request ID reused with different content")
        return dict(row), False
    identifier_review = uuid4().hex
    connection.execute("INSERT INTO eval_attempt_reviews VALUES (?,?,?,?,?,?,?,?,?)",
                       (identifier_review, queue_id, identifier, case_id, attempt, actor, review.request_id, store.now(), payload))
    return dict(connection.execute("SELECT * FROM eval_attempt_reviews WHERE id=?", (identifier_review,)).fetchone()), True


def add(identifier, case_id, attempt, review, actor):
    _bound(identifier, case_id, attempt, review)
    with store.database() as connection:
        row, _created = _append(connection, identifier, case_id, attempt, review, actor)
    return {"id": row["id"], "created_at": row["created_at"]}


def enqueue(identifier, case_id, attempt):
    detail = workspace.detail(identifier, case_id, attempt)
    if detail["result"] is None:
        raise ValueError("Missing answer cannot be reviewed")
    with store.database() as connection:
        connection.execute("INSERT OR IGNORE INTO eval_review_queue VALUES (?,?,?,?,?,'pending',NULL,NULL,NULL,NULL,?,?)",
                           (uuid4().hex, identifier, case_id, attempt, detail["rubric_id"], store.now(), store.now()))
        row = connection.execute("SELECT id FROM eval_review_queue WHERE run_id=? AND case_id=? AND attempt_no=? AND rubric_id=?",
                                 (identifier, case_id, attempt, detail["rubric_id"])).fetchone()
    return {"id": row[0]}


def list_items(status="all", offset=0, limit=20):
    with store.database() as connection:
        workspace.require_v2(connection)
        total = connection.execute("SELECT COUNT(*) FROM eval_review_queue WHERE ?='all' OR status=?", (status, status)).fetchone()[0]
        rows = connection.execute("""SELECT q.id,q.run_id,q.case_id,q.attempt_no,q.rubric_id,q.status,q.assignee,q.locked_until,
            q.first_reviewer,q.created_at,q.updated_at,c.question,e.name AS run_name,r.criteria
            FROM eval_review_queue q JOIN eval_experiments e ON e.run_id=q.run_id
            JOIN eval_case_versions c ON c.version_id=e.version_id AND c.case_id=q.case_id
            JOIN eval_rubrics r ON r.id=q.rubric_id
            WHERE ?='all' OR q.status=? ORDER BY q.updated_at DESC,q.id LIMIT ? OFFSET ?""", (status, status, limit, offset)).fetchall()
    return {"total": total, "items": [dict(row) | {"independent_human_gold": False} for row in rows]}


def claim(identifier, actor):
    with store.database() as connection:
        workspace.require_v2(connection)
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM eval_review_queue WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("Review queue item not found")
        if row["status"] in {"reviewed", "resolved"}:
            raise ValueError("Review already completed")
        if row["first_reviewer"] == actor and row["status"] == "needs_adjudication":
            raise ValueError("A different reviewer must adjudicate")
        if row["assignee"] and row["locked_until"] > time.time():
            if row["assignee"] != actor:
                raise ValueError("Review is claimed by another account")
            return dict(row)
        token = uuid4().hex
        status = "needs_adjudication" if row["status"] == "needs_adjudication" else "in_review"
        connection.execute("UPDATE eval_review_queue SET status=?,assignee=?,lock_token=?,locked_until=?,updated_at=? WHERE id=?",
                           (status, actor, token, time.time() + 900, store.now(), identifier))
        return dict(connection.execute("SELECT * FROM eval_review_queue WHERE id=?", (identifier,)).fetchone())


def complete(identifier, review, actor):
    with store.database() as connection:
        workspace.require_v2(connection)
        row = connection.execute("SELECT * FROM eval_review_queue WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("Review queue item not found")
        data = dict(row)
    detail = _bound(data["run_id"], data["case_id"], data["attempt_no"], review)
    with store.database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute("""SELECT * FROM eval_attempt_reviews WHERE queue_id=? AND actor=? AND request_id=?""",
                                      (identifier, actor, review.request_id)).fetchone()
        if previous:
            if previous["payload"] != store.canonical(review.model_dump(exclude={"lock_token"})):
                raise ValueError("Review request conflicts")
            return {"id": previous["id"], "status": connection.execute("SELECT status FROM eval_review_queue WHERE id=?", (identifier,)).fetchone()[0]}
        row = connection.execute("SELECT * FROM eval_review_queue WHERE id=?", (identifier,)).fetchone()
        if (row["assignee"] != actor or row["lock_token"] != review.lock_token or row["locked_until"] <= time.time()
                or row["status"] not in {"in_review", "needs_adjudication"}):
            raise ValueError("Review claim expired or does not belong to this account")
        adjudication = row["status"] == "needs_adjudication"
        if adjudication and (review.action != "adjudicate" or row["first_reviewer"] == actor or review.kind != "human"):
            raise ValueError("Adjudication requires a different declared human reviewer")
        if not adjudication and review.action != "review":
            raise ValueError("Initial review cannot be an adjudication")
        original = next((metric["status"] for metric in detail["metrics"] if metric["id"] == "semantic_support"), "unknown")
        decision = {"supported": "passed", "unsupported": "failed", "needs_evidence": "unknown"}[review.decision]
        conflict = review.kind != "human" or decision == "unknown" or (original != "unknown" and original != decision)
        status = "resolved" if adjudication and not conflict else "needs_adjudication" if conflict else "reviewed"
        # An adjudicator can intentionally disagree with the saved AI annotation; preserve both records.
        if adjudication and review.decision != "needs_evidence":
            status = "resolved"
        saved, _created = _append(connection, row["run_id"], row["case_id"], row["attempt_no"], review, actor, identifier)
        connection.execute("""UPDATE eval_review_queue SET status=?,assignee=NULL,lock_token=NULL,locked_until=NULL,
            first_reviewer=COALESCE(first_reviewer,?),updated_at=? WHERE id=?""", (status, actor, store.now(), identifier))
    return {"id": saved["id"], "status": status, "original_judgment_changed": False, "independent_human_gold": False}
