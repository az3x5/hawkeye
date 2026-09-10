"""Run inside the API container. Temporary language-only credential is always revoked."""
import asyncio
import json

import httpx

from app.connectors.postgres import PostgresConnector
from app.connectors.postgres.tokens import SqlAlchemyTokenStore
from app.core.config import get_settings
from app.domain.auth import Scope, new_token


async def main():
    record, secret = new_token("demo-workflow-validation", "service", [Scope.LANGUAGE], lifetime_days=None, lifetime_seconds=600)
    connector = PostgresConnector(str(get_settings().postgres_dsn))
    try:
        async with connector.session() as session:
            await SqlAlchemyTokenStore(session).add(record)
        async with httpx.AsyncClient(timeout=30, base_url="http://frontend:3000", cookies={"faceid_token": secret}) as client:
            page = await client.get("/language")
            assert page.status_code == 200 and "Media demonstration" in page.text, page.status_code
            response = await client.post("/api/demo", data={"kind": "video", "language": "en", "script": "original", "sample": "true"}, headers={"Origin": "http://frontend:3000"})
            assert response.status_code == 202, (response.status_code, response.text)
            job_id = response.json()["id"]
            for _ in range(90):
                await asyncio.sleep(2)
                response = await client.get("/api/demo")
                response.raise_for_status()
                job = next(j for j in response.json()["jobs"] if j["id"] == job_id)
                if job["state"] in ("completed", "failed"):
                    assert job["state"] == "completed", job
                    assert len(job["result"]["frames"]) == 3
                    assert job["result"]["analysis"]["text"].strip()
                    print(json.dumps({"web_page": page.status_code, "submission": 202, "job": job_id, "state": job["state"], "frames": 3, "transcript": job["result"]["analysis"]["text"], "seconds": job["result"]["elapsed_seconds"]}), flush=True)
                    break
            else:
                raise RuntimeError("Demo test did not finish in three minutes")
        async with httpx.AsyncClient(timeout=10) as client:
            assert (await client.get("http://media-demo:8020/jobs")).status_code == 401
            assert (await client.get("http://frontend:3000/api/demo")).status_code == 401
        print("Unauthenticated requests rejected.", flush=True)
    finally:
        async with connector.session() as session:
            await SqlAlchemyTokenStore(session).disable(record.token_uuid)
        await connector.close()
        print("Temporary validation credential revoked.", flush=True)


asyncio.run(main())
