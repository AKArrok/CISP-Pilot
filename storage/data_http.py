"""Data catalog endpoints share the evaluation account allowlist and Cookie/CSRF controls."""
from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from storage import data_catalog as catalog
from storage.evaluation_http import READ_EVALUATION, WRITE_EVALUATION, call

router = APIRouter(prefix="/api/data")


@router.get("/capabilities")
def capabilities(user: dict = READ_EVALUATION):
    return {"schema_version": 1 if call(catalog.ready) else 0, "model_calls": 0,
            "original_files_served": False, "automatic_promotion": False}


@router.post("/imports")
async def import_snapshot(request: Request, user: dict = WRITE_EVALUATION):
    data = bytearray()
    async for part in request.stream():
        data.extend(part)
        if len(data) > 32 * 1024 * 1024:
            raise HTTPException(413, "Snapshot exceeds 32 MiB")
    try:
        bundle = catalog.Snapshot.model_validate_json(data)
    except (ValidationError, ValueError) as exc:
        raise HTTPException(422, "Invalid knowledge snapshot") from exc
    return await run_in_threadpool(call, catalog.import_snapshot, bundle, str(user["id"]))


@router.get("/snapshots")
def snapshots(user: dict = READ_EVALUATION):
    return {"items": call(catalog.snapshots)}


@router.get("/snapshots/{identifier}")
def snapshot(identifier: str, user: dict = READ_EVALUATION):
    data = call(catalog.snapshot, identifier)
    payload = data.pop("payload")
    return data | {key: payload[key] for key in ("namespace", "label", "visibility", "sources", "artifacts", "manifest", "processor")}


@router.get("/snapshots/{identifier}/chunks")
def chunks(identifier: str, query: str = Query("", max_length=200), offset: int = Query(0, ge=0),
           limit: int = Query(20, ge=1, le=100), user: dict = READ_EVALUATION):
    return call(catalog.chunks, identifier, query, offset, limit)


@router.get("/snapshots/{identifier}/chunks/{chunk_id}")
def chunk(identifier: str, chunk_id: str, user: dict = READ_EVALUATION):
    return call(catalog.chunk, identifier, chunk_id)


@router.get("/lineage")
def lineage(snapshot: str, run: str, case: str, attempt: int = Query(1, ge=1, le=20), user: dict = READ_EVALUATION):
    return call(catalog.lineage, snapshot, run, case, attempt)


@router.get("/analysis/{run_id}")
def analysis(run_id: str, offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), user: dict = READ_EVALUATION):
    return call(catalog.analysis, run_id, offset, limit)


@router.get("/improvements")
def improvements(offset: int = Query(0, ge=0), limit: int = Query(20, ge=1, le=100), user: dict = READ_EVALUATION):
    return call(catalog.items, offset, limit)


@router.post("/improvements")
def create_item(body: catalog.Improvement, user: dict = WRITE_EVALUATION):
    return call(catalog.create_item, body, str(user["id"]))


@router.get("/improvements/{identifier}")
def item(identifier: str, user: dict = READ_EVALUATION):
    return call(catalog.item, identifier)


@router.post("/improvements/{identifier}/events")
def append_event(identifier: str, body: catalog.Event, user: dict = WRITE_EVALUATION):
    return call(catalog.append_event, identifier, body, str(user["id"]))
