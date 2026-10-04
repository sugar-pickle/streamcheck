"""Thin clients for the Sonarr and Radarr v3 APIs."""
import httpx


class ArrClient:
    def __init__(self, kind: str, url: str, api_key: str):
        self.kind = kind  # "movie" (Radarr) or "series" (Sonarr)
        self.base = url.rstrip("/")
        self._client = httpx.AsyncClient(timeout=60, headers={"X-Api-Key": api_key})

    @property
    def name(self) -> str:
        return "Radarr" if self.kind == "movie" else "Sonarr"

    @property
    def _resource(self) -> str:
        return "movie" if self.kind == "movie" else "series"

    async def close(self):
        await self._client.aclose()

    async def items(self) -> list[dict]:
        r = await self._client.get(f"{self.base}/api/v3/{self._resource}")
        r.raise_for_status()
        return [self._normalise(i) for i in r.json()]

    async def delete(self, arr_id: int, delete_files: bool, add_exclusion: bool):
        exclusion_param = "addImportExclusion" if self.kind == "movie" else "addImportListExclusion"
        r = await self._client.delete(
            f"{self.base}/api/v3/{self._resource}/{arr_id}",
            params={"deleteFiles": str(delete_files).lower(), exclusion_param: str(add_exclusion).lower()},
        )
        r.raise_for_status()

    def _normalise(self, i: dict) -> dict:
        poster = next((img.get("remoteUrl") for img in i.get("images", [])
                       if img.get("coverType") == "poster"), None)
        if self.kind == "movie":
            size = i.get("sizeOnDisk") or 0
            has_files = bool(i.get("hasFile"))
            link = f"{self.base}/movie/{i.get('titleSlug') or i.get('tmdbId')}"
        else:
            stats = i.get("statistics") or {}
            size = stats.get("sizeOnDisk") or 0
            has_files = (stats.get("episodeFileCount") or 0) > 0
            link = f"{self.base}/series/{i.get('titleSlug')}"
        return {
            "key": f"{self.kind}:{i['id']}",
            "kind": self.kind,
            "arr_id": i["id"],
            "title": i["title"],
            "year": i.get("year") or None,
            "tmdb_id": i.get("tmdbId") or None,
            "imdb_id": i.get("imdbId") or None,
            "tvdb_id": i.get("tvdbId") or None,
            "size": size,
            "has_files": has_files,
            "monitored": i.get("monitored", False),
            "poster": poster,
            "arr_url": link,
        }
