"""Minimal client for JustWatch's (unofficial) public GraphQL API."""
import asyncio
import re
import unicodedata

import httpx

GRAPHQL_URL = "https://apis.justwatch.com/graphql"
IMAGE_BASE = "https://images.justwatch.com"
SITE_BASE = "https://www.justwatch.com"

PACKAGES_QUERY = """
query Packages($country: Country!) {
  packages(country: $country, platform: WEB) {
    packageId clearName technicalName icon(profile: S100)
  }
}"""

SEARCH_QUERY = """
query Search($q: String!, $country: Country!, $language: Language!, $types: [ObjectType!]) {
  popularTitles(country: $country, first: 6, filter: {searchQuery: $q, objectTypes: $types}) {
    edges { node {
      id objectType
      content(country: $country, language: $language) {
        title originalReleaseYear fullPath externalIds { imdbId tmdbId }
      }
      offers(country: $country, platform: WEB) {
        monetizationType standardWebURL
        package { packageId clearName icon(profile: S100) }
      }
    } }
  }
}"""


def icon_url(path: str | None) -> str | None:
    return f"{IMAGE_BASE}{path.replace('{format}', 'webp')}" if path else None


def _norm(title: str) -> str:
    t = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode().lower()
    t = re.sub(r"\(\d{4}\)", "", t)
    t = re.sub(r"^(the|a|an)\s+", "", t)
    return re.sub(r"[^a-z0-9]", "", t)


class JustWatch:
    def __init__(self, country: str, language: str = "en", concurrency: int = 4):
        self.country = country.upper()
        self.language = language
        self._sem = asyncio.Semaphore(concurrency)
        self._client = httpx.AsyncClient(
            timeout=20,
            headers={"User-Agent": "streamcheck/1.0", "Content-Type": "application/json"},
        )

    async def close(self):
        await self._client.aclose()

    async def _gql(self, query: str, variables: dict) -> dict:
        async with self._sem:
            for attempt in range(4):
                r = await self._client.post(GRAPHQL_URL, json={"query": query, "variables": variables})
                if r.status_code == 429 or r.status_code >= 500:
                    await asyncio.sleep(2 ** attempt)
                    continue
                r.raise_for_status()
                body = r.json()
                if body.get("errors"):
                    raise RuntimeError(body["errors"][0].get("message", "GraphQL error"))
                return body["data"]
            r.raise_for_status()
            raise RuntimeError(f"JustWatch request failed ({r.status_code})")

    async def packages(self) -> list[dict]:
        data = await self._gql(PACKAGES_QUERY, {"country": self.country})
        seen, out = set(), []
        for p in data["packages"]:
            if p["packageId"] in seen:
                continue
            seen.add(p["packageId"])
            out.append({
                "id": p["packageId"],
                "name": p["clearName"],
                "technical_name": p["technicalName"],
                "icon": icon_url(p.get("icon")),
            })
        return out

    async def find(self, kind: str, title: str, year: int | None,
                   tmdb_id: int | None, imdb_id: str | None) -> dict | None:
        """Find the JustWatch entry for a library item and return its offers."""
        data = await self._gql(SEARCH_QUERY, {
            "q": title,
            "country": self.country,
            "language": self.language,
            "types": ["MOVIE" if kind == "movie" else "SHOW"],
        })
        nodes = [e["node"] for e in data["popularTitles"]["edges"]]
        node = self._best_match(nodes, title, year, tmdb_id, imdb_id)
        if not node:
            return None

        offers, seen = [], set()
        for o in node.get("offers") or []:
            pkg = o["package"]
            key = (pkg["packageId"], o["monetizationType"])
            if key in seen:
                continue
            seen.add(key)
            offers.append({
                "provider_id": pkg["packageId"],
                "provider": pkg["clearName"],
                "icon": icon_url(pkg.get("icon")),
                "type": o["monetizationType"],
                "url": o.get("standardWebURL"),
            })
        path = node["content"].get("fullPath")
        return {
            "id": node["id"],
            "title": node["content"]["title"],
            "url": f"{SITE_BASE}{path}" if path else None,
            "offers": offers,
        }

    @staticmethod
    def _best_match(nodes, title, year, tmdb_id, imdb_id):
        for n in nodes:
            ext = n["content"].get("externalIds") or {}
            if tmdb_id and ext.get("tmdbId") and str(ext["tmdbId"]) == str(tmdb_id):
                return n
            if imdb_id and ext.get("imdbId") == imdb_id:
                return n
        # Fall back to a normalised title + year match (allowing off-by-one years).
        want = _norm(title)
        for n in nodes:
            c = n["content"]
            if _norm(c["title"]) != want:
                continue
            if not year or not c.get("originalReleaseYear") or abs(c["originalReleaseYear"] - year) <= 1:
                return n
        return None
