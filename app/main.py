import asyncio
import base64
import json
import logging
import os
import secrets
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .arr import ArrClient
from .justwatch import JustWatch

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("streamcheck")

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
SETTINGS_FILE = DATA_DIR / "settings.json"
RESULTS_FILE = DATA_DIR / "results.json"
STATIC_DIR = Path(__file__).parent / "static"

DEFAULT_SETTINGS = {
    "country": os.getenv("COUNTRY", "GB").upper(),
    "language": os.getenv("LANGUAGE", "en"),
    "providers": [],                   # JustWatch package ids
    "monetization": ["FLATRATE", "FREE", "ADS"],
}
AUTH_USER = os.getenv("AUTH_USERNAME")
AUTH_PASS = os.getenv("AUTH_PASSWORD")
SCAN_INTERVAL_HOURS = float(os.getenv("SCAN_INTERVAL_HOURS", "0"))


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, data):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(path)


def arr_clients() -> list[ArrClient]:
    clients = []
    if os.getenv("RADARR_URL") and os.getenv("RADARR_API_KEY"):
        clients.append(ArrClient("movie", os.environ["RADARR_URL"], os.environ["RADARR_API_KEY"]))
    if os.getenv("SONARR_URL") and os.getenv("SONARR_API_KEY"):
        clients.append(ArrClient("series", os.environ["SONARR_URL"], os.environ["SONARR_API_KEY"]))
    return clients


class State:
    def __init__(self):
        self.settings = {**DEFAULT_SETTINGS, **load_json(SETTINGS_FILE, {})}
        self.results = load_json(RESULTS_FILE, {"items": [], "country": None, "finished_at": None})
        self.scan = {"running": False, "done": 0, "total": 0, "phase": "idle", "error": None}
        self.lock = asyncio.Lock()


state = State()


async def run_scan():
    if state.lock.locked():
        return
    async with state.lock:
        state.scan = {"running": True, "done": 0, "total": 0, "phase": "Fetching libraries", "error": None}
        clients = arr_clients()
        jw = JustWatch(state.settings["country"], state.settings["language"])
        try:
            if not clients:
                raise RuntimeError("No Sonarr/Radarr configured. Set *_URL and *_API_KEY env vars.")
            items, errors = [], []
            for c in clients:
                try:
                    items.extend(await c.items())
                except Exception as e:
                    log.exception("Failed to fetch %s library", c.name)
                    errors.append(f"{c.name}: {e}")
            state.scan.update(total=len(items), phase="Checking JustWatch")

            async def check(item):
                try:
                    item["justwatch"] = await jw.find(item["kind"], item["title"], item["year"],
                                                      item["tmdb_id"], item["imdb_id"])
                except Exception as e:
                    log.warning("JustWatch lookup failed for %s: %s", item["title"], e)
                    item["justwatch"] = None
                    item["lookup_error"] = str(e)
                state.scan["done"] += 1

            await asyncio.gather(*(check(i) for i in items))
            state.results = {
                "items": items,
                "country": state.settings["country"],
                "finished_at": time.time(),
            }
            save_json(RESULTS_FILE, state.results)
            state.scan.update(phase="Complete", error="; ".join(errors) or None)
            log.info("Scan complete: %d items", len(items))
        except Exception as e:
            log.exception("Scan failed")
            state.scan.update(phase="Failed", error=str(e))
        finally:
            state.scan["running"] = False
            await jw.close()
            for c in clients:
                await c.close()


async def scheduler():
    while True:
        await asyncio.sleep(SCAN_INTERVAL_HOURS * 3600)
        await run_scan()


@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(scheduler()) if SCAN_INTERVAL_HOURS > 0 else None
    yield
    if task:
        task.cancel()


app = FastAPI(title="StreamCheck", lifespan=lifespan)


@app.middleware("http")
async def basic_auth(request: Request, call_next):
    if AUTH_USER and AUTH_PASS:
        ok = False
        header = request.headers.get("authorization", "")
        if header.lower().startswith("basic "):
            try:
                user, _, pw = base64.b64decode(header[6:]).decode().partition(":")
                ok = secrets.compare_digest(user, AUTH_USER) and secrets.compare_digest(pw, AUTH_PASS)
            except Exception:
                ok = False
        if not ok:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="StreamCheck"'})
    return await call_next(request)


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/api/status")
async def status():
    return {
        "scan": state.scan,
        "configured": [c.name for c in arr_clients()],
        "last_scan": state.results.get("finished_at"),
        "results_country": state.results.get("country"),
    }


@app.get("/api/settings")
async def get_settings():
    return state.settings


class SettingsIn(BaseModel):
    country: str | None = None
    providers: list[int] | None = None
    monetization: list[str] | None = None


@app.put("/api/settings")
async def put_settings(body: SettingsIn):
    updates = body.model_dump(exclude_none=True)
    if "country" in updates:
        updates["country"] = updates["country"].upper()
    state.settings.update(updates)
    save_json(SETTINGS_FILE, state.settings)
    return state.settings


_provider_cache: dict[str, tuple[float, list]] = {}


@app.get("/api/providers")
async def providers():
    country = state.settings["country"]
    cached = _provider_cache.get(country)
    if cached and time.time() - cached[0] < 86400:
        return cached[1]
    jw = JustWatch(country)
    try:
        pkgs = await jw.packages()
    except Exception as e:
        raise HTTPException(502, f"Could not load providers from JustWatch: {e}")
    finally:
        await jw.close()
    _provider_cache[country] = (time.time(), pkgs)
    return pkgs


@app.get("/api/results")
async def results():
    return state.results


@app.post("/api/scan", status_code=202)
async def start_scan():
    if state.scan["running"]:
        raise HTTPException(409, "A scan is already running")
    asyncio.create_task(run_scan())
    return {"started": True}


class DeleteIn(BaseModel):
    keys: list[str]
    delete_files: bool = True
    add_exclusion: bool = False


@app.post("/api/delete")
async def delete(body: DeleteIn):
    clients = {c.kind: c for c in arr_clients()}
    by_key = {i["key"]: i for i in state.results["items"]}
    deleted, failed = [], []
    try:
        for key in body.keys:
            item = by_key.get(key)
            client = clients.get(item["kind"]) if item else None
            if not client:
                failed.append({"key": key, "error": "Unknown item or service not configured"})
                continue
            try:
                await client.delete(item["arr_id"], body.delete_files, body.add_exclusion)
                deleted.append(key)
                log.info("Deleted %s '%s' from %s (files=%s)", item["kind"], item["title"],
                         client.name, body.delete_files)
            except Exception as e:
                log.warning("Failed to delete %s: %s", item["title"], e)
                failed.append({"key": key, "error": str(e)})
    finally:
        for c in clients.values():
            await c.close()
    if deleted:
        gone = set(deleted)
        state.results["items"] = [i for i in state.results["items"] if i["key"] not in gone]
        save_json(RESULTS_FILE, state.results)
    return {"deleted": deleted, "failed": failed}
