"""Allowlisted evaluation workspace, using the application's Cookie and CSRF boundary."""
import json
import os
import sqlite3

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from storage import evaluation_store as store
from storage import evaluation_workspace as workspace, evaluation_jobs as jobs, evaluation_reviews as reviews
from storage.http_auth import USER_DEPENDENCY, WRITE_DEPENDENCY

router = APIRouter(prefix="/api/evaluation")


def allowed(user):
    identifiers = {value.strip() for value in os.getenv("CISP_EVAL_USER_IDS", "").split(",") if value.strip()}
    if str(user["id"]) not in identifiers:
        raise HTTPException(403, "Evaluation access not granted")
    return user


def reader(user: dict = USER_DEPENDENCY):
    return allowed(user)


def writer(user: dict = WRITE_DEPENDENCY):
    return allowed(user)


READ_EVALUATION = Depends(reader)
WRITE_EVALUATION = Depends(writer)


def call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except KeyError as exc:
        raise HTTPException(404, "Evaluation record not found") from exc
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, "Invalid or conflicting evaluation data") from exc
    except sqlite3.Error as exc:
        raise HTTPException(503, "Evaluation storage unavailable") from exc


@router.get("/runs")
def runs(user: dict = READ_EVALUATION):
    return {"items": call(store.list_runs), "user": user}


@router.post("/imports")
async def import_run(request: Request, user: dict = WRITE_EVALUATION):
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(413, "Import exceeds 8 MiB")
    try:
        bundle = workspace.parse_bundle(data)
    except ValidationError as exc:
        raise HTTPException(422, "Invalid evaluation bundle; check schema, IDs and answer hashes") from exc
    return await run_in_threadpool(call, store.import_bundle, bundle, str(user["id"]))


@router.get("/compare")
def compare(left: str, right: str, user: dict = READ_EVALUATION):
    return call(store.compare, left, right)


@router.get("/runs/{identifier}")
def run(identifier: str, user: dict = READ_EVALUATION):
    row = call(store.get_run, identifier)
    with store.database() as connection:
        summary = call(workspace.summary, connection, identifier) if workspace.ready(connection) else store.summary(row)
    return {**summary, "config": row["payload"]["config"],
            "criteria": row["payload"]["criteria"], "artifacts": row["payload"]["artifacts"]}


@router.get("/runs/{identifier}/cases")
def cases(identifier: str, status: str = Query("all", pattern="^(all|failed|passed|unknown)$"),
          query: str = Query("", max_length=200), offset: int = Query(0, ge=0),
          limit: int = Query(20, ge=1, le=100), user: dict = READ_EVALUATION):
    return call(store.cases, identifier, status, query, offset, limit)


@router.get("/runs/{identifier}/cases/{case_id}")
def case(identifier: str, case_id: str, attempt: int = Query(1, ge=1, le=20), user: dict = READ_EVALUATION):
    if workspace.ready():
        return call(workspace.detail, identifier, case_id, attempt)
    if attempt != 1:
        raise HTTPException(404, "Attempt not found")
    return call(store.detail, identifier, case_id)


@router.post("/runs/{identifier}/cases/{case_id}/reviews")
def review(identifier: str, case_id: str, body: store.Review, user: dict = WRITE_EVALUATION):
    return call(store.add_review, identifier, case_id, body, str(user["id"]))


@router.post("/runs/{identifier}/checks")
def check(identifier: str, user: dict = WRITE_EVALUATION):
    return call(store.offline_check, identifier, str(user["id"]))


@router.get("/runs/{identifier}/export")
def export(identifier: str, format: str = Query("json", pattern="^(json|markdown)$"), user: dict = READ_EVALUATION):
    report = call(workspace.export_report if workspace.ready() else store.export_report, identifier)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if format == "markdown":
        text = "# CISP 测评报告\n\n脱敏开发证据，不构成独立人工金标或生产验收。\n\n```json\n" + text + "\n```\n"
    return Response(text, media_type="application/json" if format == "json" else "text/markdown",
                    headers={"Content-Disposition": f'attachment; filename="evaluation-{identifier[:12]}.{ "json" if format == "json" else "md"}"'})


@router.get("/capabilities")
def capabilities(user: dict = READ_EVALUATION):
    return {"schema_version": 2 if call(workspace.ready) else 1,
            "worker_enabled": os.getenv("CISP_EVAL_WORKER_ENABLED", "false").lower() == "true",
            "offline_commands": [jobs.COMMAND], "model_calls": 0}


@router.get("/datasets")
def datasets(user: dict = READ_EVALUATION):
    return {"items": call(workspace.datasets)}


@router.get("/compare-detail")
def compare_detail(left: str, right: str, case_id: str, left_attempt: int = Query(1, ge=1, le=20),
                   right_attempt: int = Query(1, ge=1, le=20), user: dict = READ_EVALUATION):
    return call(workspace.comparison_detail, left, right, case_id, left_attempt, right_attempt)


@router.post("/runs/{identifier}/cases/{case_id}/attempts/{attempt}/reviews")
def attempt_review(identifier: str, case_id: str, attempt: int, body: reviews.AttemptReview, user: dict = WRITE_EVALUATION):
    return call(reviews.add, identifier, case_id, attempt, body, str(user["id"]))


@router.post("/runs/{identifier}/cases/{case_id}/attempts/{attempt}/queue")
def enqueue(identifier: str, case_id: str, attempt: int, user: dict = WRITE_EVALUATION):
    return call(reviews.enqueue, identifier, case_id, attempt)


@router.get("/review-queue")
def queue(status: str = Query("all", pattern="^(all|pending|in_review|reviewed|needs_adjudication|resolved)$"),
          offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), user: dict = READ_EVALUATION):
    return call(reviews.list_items, status, offset, limit)


@router.post("/review-queue/{identifier}/claim")
def claim_review(identifier: str, user: dict = WRITE_EVALUATION):
    return call(reviews.claim, identifier, str(user["id"]))


@router.post("/review-queue/{identifier}/complete")
def complete_review(identifier: str, body: reviews.QueueDecision, user: dict = WRITE_EVALUATION):
    return call(reviews.complete, identifier, body, str(user["id"]))


@router.post("/runs/{identifier}/jobs")
def create_job(identifier: str, body: jobs.JobRequest, user: dict = WRITE_EVALUATION):
    return call(jobs.create, identifier, body, str(user["id"]))


@router.get("/jobs")
def job_list(user: dict = READ_EVALUATION):
    return {"items": call(jobs.list_jobs)}


@router.get("/jobs/{identifier}")
def job_detail(identifier: str, user: dict = READ_EVALUATION):
    return call(jobs.get, identifier)


@router.post("/jobs/{identifier}/cancel")
def cancel_job(identifier: str, user: dict = WRITE_EVALUATION):
    return call(jobs.cancel, identifier)


@router.post("/jobs/{identifier}/resume")
def resume_job(identifier: str, user: dict = WRITE_EVALUATION):
    return call(jobs.resume, identifier)
