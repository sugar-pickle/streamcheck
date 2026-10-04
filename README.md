# StreamCheck

Scans your Radarr and Sonarr libraries, checks [JustWatch](https://www.justwatch.com) for each title,
and shows which ones are available on the streaming services you subscribe to. Select titles in the
web UI and delete them from Radarr/Sonarr (optionally with their files) to free up space.

## Quick start

```bash
cp .env.example .env      # fill in URLs + API keys
docker compose up -d --build
```

Open http://localhost:8080, then:

1. **Providers**: pick your country and tick the services you have (Netflix, Disney+, iPlayer…).
   Choose which offer types count as "available" (subscription, free, free with ads, rent, buy).
2. **Scan library**: fetches every movie/series and looks each one up on JustWatch.
3. Filter, sort by size, select titles, then **Delete selected**. You choose whether to delete files
   and whether to add an import-list exclusion.

Changing services or offer types re-filters instantly without a rescan, because the scan stores all
offers. Changing country needs a rescan.

## Configuration

| Variable | Description |
|---|---|
| `RADARR_URL`, `RADARR_API_KEY` | Radarr base URL and API key (optional if you only use Sonarr) |
| `SONARR_URL`, `SONARR_API_KEY` | Sonarr base URL and API key (optional if you only use Radarr) |
| `COUNTRY` | Default JustWatch country, e.g. `GB`, `US` (default `GB`) |
| `SCAN_INTERVAL_HOURS` | Auto-rescan interval; `0` disables (default) |
| `AUTH_USERNAME`, `AUTH_PASSWORD` | Enables HTTP basic auth. **Recommended**, because the UI can delete media |

Settings and the last scan results are saved in `./data`.

If Sonarr/Radarr run in the same compose stack or Docker network, use their service names
(e.g. `http://radarr:7878`). Otherwise use the host IP.

## How matching works

Each title is searched on JustWatch's GraphQL API and matched by TMDB ID, then IMDb ID, then by
normalised title and year (±1). Titles with no match show "Not on JustWatch" when
**Show unavailable** is on.

## Notes

- JustWatch's API is unofficial and undocumented. It needs no key but may change without notice.
  Requests are rate-limited to 4 at a time with backoff.
- Series availability is per show, not per season. A service may carry only some seasons.
