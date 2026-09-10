"""Authenticated, serialized media demo jobs; retains evidence and status across restarts."""
import json
import os
import sqlite3
import subprocess
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, UploadFile

ROOT = Path(os.environ.get("DEMO_DATA", "/results"))
POOL = ThreadPoolExecutor(max_workers=1)
LOCK = threading.Lock()


def db():
    connection = sqlite3.connect(ROOT / "jobs.sqlite3", timeout=20)
    connection.row_factory = sqlite3.Row
    return connection


@asynccontextmanager
async def lifespan(app):
    ROOT.mkdir(parents=True, exist_ok=True)
    (ROOT / "inputs").mkdir(exist_ok=True)
    with db() as con:
        con.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT, filename TEXT, state TEXT, result TEXT, error TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP)")
        con.execute("UPDATE jobs SET state='failed', error='Service restarted; submit this sample again.' WHERE state IN ('queued','running')")
    yield


app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None)


async def identity(authorization: Annotated[str | None, Header()] = None):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Sign in to continue.")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(os.environ.get("DEMO_API_URL", "http://api:8000") + "/api/v1/me", headers={"Authorization": authorization})
    except httpx.HTTPError:
        raise HTTPException(503, "Authentication service unavailable.") from None
    if response.status_code != 200:
        raise HTTPException(401 if response.status_code in (401,403) else 503, "Session invalid or authentication service unavailable.")
    user = response.json()
    if "language" not in user.get("scopes", []):
        raise HTTPException(403, "Language permission required.")
    return user["subject"]


def public(row):
    value = dict(row)
    value.pop("owner", None)
    value["result"] = json.loads(value["result"]) if value["result"] else None
    return value


def execute(job, kind, path, language, script):
    with db() as con:
        con.execute("UPDATE jobs SET state='running' WHERE id=?", (job,))
    try:
        process = subprocess.run(["python", "/demo/runner.py", kind, str(path), "--language", language, "--output-script", script], capture_output=True, text=True, timeout=900)
        if process.returncode:
            raise RuntimeError("Media decoding or model inference failed. Check the file format and model availability.")
        result = json.loads(process.stdout.rsplit("\nSaved ", 1)[0])
        with db() as con:
            con.execute("UPDATE jobs SET state='completed',result=? WHERE id=?", (json.dumps(result, ensure_ascii=False), job))
    except Exception as exc:
        message = "Processing exceeded the 15-minute demo limit." if isinstance(exc, subprocess.TimeoutExpired) else str(exc)
        with db() as con:
            con.execute("UPDATE jobs SET state='failed',error=? WHERE id=?", (message[:500], job))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/jobs")
def jobs(owner=Depends(identity)):
    with db() as con:
        return {"jobs": [public(row) for row in con.execute("SELECT * FROM jobs WHERE owner=? ORDER BY created DESC,rowid DESC LIMIT 30", (owner,))]}


@app.get("/jobs/{job_id}")
def job(job_id: UUID, owner=Depends(identity)):
    with db() as con:
        row = con.execute("SELECT * FROM jobs WHERE id=? AND owner=?", (str(job_id), owner)).fetchone()
    if row is None:
        raise HTTPException(404, "Job not found.")
    return public(row)


@app.post("/jobs", status_code=202)
async def submit(
    kind: Annotated[Literal["image", "video", "audio", "ocr"], Form()],
    language: Annotated[Literal["en", "dv"], Form()] = "en",
    script: Annotated[Literal["original", "thaana", "latin"], Form()] = "original",
    sample: Annotated[bool, Form()] = False,
    file: Annotated[UploadFile | None, File()] = None,
    owner=Depends(identity),
):
    # Demo quota bounds disk, queue, and decode load; administrators can archive/reset
    # this dedicated dataset separately from the application's production media.
    job_id = str(uuid4())
    if sample:
        if kind == "ocr" or language != "en":
            raise HTTPException(422, "Built-in samples support English image, audio and video tests.")
        path = ROOT / {"image": "sample.png", "audio": "sample.wav", "video": "sample.mp4"}[kind]
        if not path.exists():
            raise HTTPException(503, "Demonstration samples are not installed.")
        filename = path.name
    else:
        if file is None:
            raise HTTPException(422, "Choose a file.")
        filename = Path(file.filename or "upload").name[:150]
        extension = Path(filename).suffix.lower()
        allowed = {"image": {".png", ".jpg", ".jpeg", ".webp"}, "ocr": {".png", ".jpg", ".jpeg", ".webp"}, "audio": {".wav", ".mp3", ".m4a", ".flac", ".ogg"}, "video": {".mp4", ".webm", ".mov"}}
        if extension not in allowed[kind]:
            raise HTTPException(422, "Unsupported file extension for selected analysis.")
        content = await file.read(20 * 1024 * 1024 + 1)
        if not content or len(content) > 20 * 1024 * 1024:
            raise HTTPException(413, "Choose a non-empty file below 20 MB.")
        path = ROOT / "inputs" / f"{job_id}{extension}"
    with LOCK, db() as con:
        if con.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] >= 100:
            raise HTTPException(429, "Demo dataset limit reached (100 jobs). Archive it before adding more.")
        if con.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0] >= 8:
            raise HTTPException(429, "Demo queue is full. Wait for a job to finish.")
        if not sample:
            path.write_bytes(content)
        con.execute("INSERT INTO jobs (id,owner,kind,filename,state) VALUES (?,?,?,?, 'queued')", (job_id, owner, kind, filename))
    POOL.submit(execute, job_id, kind, path, language, script)
    return {"id": job_id, "state": "queued"}
