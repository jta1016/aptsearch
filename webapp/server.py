"""
Apartment Search web server.
Proxies to the Apify actor, stores preferences locally.

Run with: uvicorn server:app --reload --port 8787
"""
import json
import os
import httpx
from pathlib import Path
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel

APIFY_TOKEN = os.environ.get("APIFY_TOKEN", "")
ACTOR_ID = "comfy-classmate~aptsearch"
APIFY_BASE = "https://api.apify.com/v2"
PREFS_FILE = Path(__file__).parent / "preferences.json"

app = FastAPI()


# ── Preferences ──────────────────────────────────────────────────────────────

class Preferences(BaseModel):
    email: str = ""
    zipcodes: list[str] = []
    neighborhoods: list[str] = []
    min_price: int | None = None
    max_price: int | None = None
    target_price: int | None = None
    min_bedrooms: int = 1
    max_bedrooms: int | None = None
    min_bathrooms: float = 1
    pets_allowed: bool = False
    required_amenities: list[str] = []
    max_subway_distance_miles: float = 0.5
    preferred_subway_lines: list[str] = []
    availability_before: str | None = None
    results_per_run: int = 20
    sites: list[str] = ["craigslist", "padmapper", "streeteasy"]


def load_prefs() -> dict:
    if PREFS_FILE.exists():
        return json.loads(PREFS_FILE.read_text())
    return Preferences().model_dump()


def save_prefs(data: dict):
    PREFS_FILE.write_text(json.dumps(data, indent=2))


@app.get("/api/preferences")
def get_preferences():
    return load_prefs()


@app.post("/api/preferences")
def set_preferences(prefs: Preferences):
    save_prefs(prefs.model_dump())
    return {"ok": True}


# ── Apify ─────────────────────────────────────────────────────────────────────

def apify_headers():
    if not APIFY_TOKEN:
        raise HTTPException(500, "APIFY_TOKEN not set")
    return {"Authorization": f"Bearer {APIFY_TOKEN}"}


@app.post("/api/run")
async def trigger_run():
    prefs = load_prefs()
    actor_input = prefs

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            f"{APIFY_BASE}/acts/{ACTOR_ID}/runs",
            headers=apify_headers(),
            json={"input": actor_input},
        )
        if resp.status_code not in (200, 201):
            raise HTTPException(resp.status_code, resp.text)
        run = resp.json().get("data", {})

    return {"run_id": run.get("id"), "status": run.get("status")}


@app.get("/api/run/{run_id}/status")
async def run_status(run_id: str):
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(
            f"{APIFY_BASE}/actor-runs/{run_id}",
            headers=apify_headers(),
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
    return {"status": data.get("status"), "dataset_id": data.get("defaultDatasetId")}


@app.get("/api/run/{run_id}/results")
async def run_results(run_id: str):
    # First get the dataset ID
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{APIFY_BASE}/actor-runs/{run_id}",
            headers=apify_headers(),
        )
        r.raise_for_status()
        dataset_id = r.json().get("data", {}).get("defaultDatasetId")

        if not dataset_id:
            return {"items": []}

        r2 = await client.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            headers=apify_headers(),
            params={"clean": True},
        )
        r2.raise_for_status()
        return {"items": r2.json()}


@app.api_route("/api/apify", methods=["GET", "POST", "PUT", "DELETE", "PATCH"])
async def apify_proxy(request: Request, path: str = ""):
    """Generic proxy to Apify API — keeps the token server-side."""
    if not APIFY_TOKEN:
        raise HTTPException(500, "APIFY_TOKEN not set")

    url = f"{APIFY_BASE}{path}"
    body = await request.body()

    forward_headers = {"Authorization": f"Bearer {APIFY_TOKEN}"}
    if request.headers.get("content-type"):
        forward_headers["Content-Type"] = request.headers["content-type"]

    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.request(
            method=request.method,
            url=url,
            content=body or None,
            headers=forward_headers,
            params={k: v for k, v in request.query_params.items() if k != "path"},
        )

    return Response(
        content=resp.content,
        status_code=resp.status_code,
        media_type=resp.headers.get("content-type", "application/json"),
    )


@app.get("/api/results/latest")
async def latest_results():
    """Get results from the most recent run."""
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.get(
            f"{APIFY_BASE}/acts/{ACTOR_ID}/runs/last",
            headers=apify_headers(),
        )
        if r.status_code == 404:
            return {"items": [], "status": "no runs yet"}
        r.raise_for_status()
        data = r.json().get("data", {})
        status = data.get("status")
        dataset_id = data.get("defaultDatasetId")

        if not dataset_id or status not in ("SUCCEEDED",):
            return {"items": [], "status": status}

        r2 = await client.get(
            f"{APIFY_BASE}/datasets/{dataset_id}/items",
            headers=apify_headers(),
            params={"clean": True},
        )
        r2.raise_for_status()
        return {"items": r2.json(), "status": status}


# ── Static ────────────────────────────────────────────────────────────────────

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
def index():
    return FileResponse("static/index.html")
