# Data model

The schema is defined in `collector/db/models.py` and created by Alembic migrations in
`migrations/` (`alembic upgrade head`). It works on SQLite (local) and Postgres (Neon).

Conventions:

- **Money** is integer cents in US dollars (`1999` = $19.99).
- **Timestamps** (`*_at`) are timezone-aware UTC. On SQLite they are stored as UTC text
  and read back with the UTC timezone attached.
- **Dates** (`release_date`, `starts_on`, `ends_on`) are plain calendar dates.

Sources:

- **Steam** — `store.steampowered.com/api/appdetails?cc=us`, either one game with full
  details or up to 100 games per call with `filters=price_overview`; the game list from
  `api.steampowered.com/IStoreService/GetAppList` (needs `STEAM_API_KEY`); and
  `store.steampowered.com/api/featuredcategories` (`collector/sources/steam.py`).
- **SteamSpy** — the `top100in2weeks`, `top100forever`, and `top100owned` lists
  (`collector/sources/steamspy.py`).
- **ITAD** — IsThereAnyDeal API, `games/lookup/v1` and `games/history/v2?country=US`
  (`collector/sources/itad.py`).
- **Old DB** — rows copied from the pre-Alembic SQLite file by `scripts/migrate_sqlite.py`.

## Jobs

| Job | Schedule | Reads | Writes |
| --- | --- | --- | --- |
| `collector.jobs.catalog` | daily | GetAppList, SteamSpy top lists, featured categories | new `games` (ID and name only) and `poll_state` rows; `poll_state.tier` |
| `collector.jobs.prices` | every 3 hours | due `poll_state` rows; appdetails | `price_changes` (steam, only on change), `poll_state`, and for new/stale games `games` + `game_genres` |
| `collector.jobs.itad` | daily, up to 300 games | games with no `poll_state.itad_checked_at`, hot then most-reviewed; ITAD lookup and history | `itad_games`, `price_changes` (itad), `poll_state.itad_checked_at` |

## games

One row per Steam game. The catalog job adds new games with just the app ID and name;
the prices job fills in the rest from a full appdetails call when the game is new or its
details are more than 30 days old.

| Column | Type | Source / meaning |
| --- | --- | --- |
| `app_id` | integer, PK | Steam app ID. |
| `title` | text | Steam `name`. |
| `developer` | text, nullable | First entry of Steam `developers`. |
| `publisher` | text, nullable | First entry of Steam `publishers`. |
| `release_date` | date, nullable | Steam `release_date.date` parsed from text like `"Oct 21, 2020"`. NULL when Steam gives no exact day (`"Q1 2027"`, `"August 2026"`, `"Coming soon"`, empty). |
| `metacritic_score` | integer, nullable | Steam `metacritic.score`. |
| `total_reviews` | integer, nullable | Steam `recommendations.total`. |
| `is_free` | boolean | Steam `is_free`. |
| `type` | text | Steam app `type`. `"game"` for everything the catalog adds; if a details fetch shows something else (e.g. `"dlc"`), the type is saved and the prices job stops checking it. |
| `created_at` | UTC timestamp | When we first stored the game. Migrated rows use their first old price snapshot time. |
| `updated_at` | UTC timestamp | Last time the metadata was refreshed. Migrated rows use the old `last_updated`. |
| `details_checked_at` | UTC timestamp, nullable | Last full appdetails fetch. NULL means only the catalog's ID and name are known, so the prices job will fetch details. Existing rows were backfilled from `updated_at`. |

## genres

Distinct genre names.

| Column | Type | Source / meaning |
| --- | --- | --- |
| `id` | integer, PK | Our own ID. |
| `name` | text, unique | Steam `genres[].description`, e.g. `"Action"`. |

## game_genres

Links games to genres (many-to-many). Replaced in full each time a game is fetched.
Migrated rows come from the old comma-separated `games.genres` text.

| Column | Type | Source / meaning |
| --- | --- | --- |
| `app_id` | integer, PK, FK → `games` | |
| `genre_id` | integer, PK, FK → `genres` | |

## price_changes

The price time series. The prices job writes a `steam` row only when `initial_cents`,
`final_cents`, or `discount_pct` differ from the game's latest row from any source;
`itad` rows are ITAD's own list of price changes. Unique on `(app_id, observed_at, source)`,
so re-running a job cannot create duplicates. Indexed on `(app_id, observed_at)`.

Because migrated ITAD rows have no `initial_cents`, a game whose latest row is one of them
gets one extra `steam` row on its first check even if the price is the same.

| Column | Type | Source / meaning |
| --- | --- | --- |
| `id` | integer, PK | |
| `app_id` | integer, FK → `games` | |
| `observed_at` | UTC timestamp | `steam`: when the collector saw this price. `itad`: when ITAD says the price took effect. |
| `initial_cents` | integer, nullable | Full (undiscounted) price. `steam`: `price_overview.initial`. `itad`: `deal.regular`. NULL for ITAD rows migrated from the old DB, which never stored the regular price. |
| `final_cents` | integer | Price actually charged. `steam`: `price_overview.final`. `itad`: `deal.price`. |
| `discount_pct` | integer, 0–100 | `steam`: computed from `initial_cents` and `final_cents` (Steam's own `discount_percent` is sometimes wrong). `itad`: ITAD `deal.cut`. |
| `source` | text | `"steam"` (our own polling) or `"itad"` (history backfill). Only the Steam store's history is kept from ITAD. |

Games with no price (free, region-locked, unreleased) get no rows here.

## poll_state

One row per game: when it was last checked and when it is due next. The prices job picks
games with `next_check_at` in the past, hot tier first, then the longest-waiting. Every
check, successful or not, updates `last_checked_at` and schedules the next one: 6 hours
later for hot games, 2 days later for normal ones.

| Column | Type | Source / meaning |
| --- | --- | --- |
| `app_id` | integer, PK, FK → `games` | |
| `tier` | text | `"hot"` if the game is in a SteamSpy top-100 list or a Steam featured category at the last catalog run, else `"normal"`. Games that become hot are due immediately. If a hot source is unreachable, the catalog job doesn't demote anything. |
| `last_checked_at` | UTC timestamp, nullable | Last time the prices job checked this game. |
| `next_check_at` | UTC timestamp, nullable | When the game is next due. New games (catalog) and migrated games are due immediately. Indexed. |
| `last_error` | text, nullable | Why the last check failed (e.g. `"/api/appdetails: rate limited (HTTP 429)"`, or `success=false` when the store doesn't sell the game in the US); cleared on success. |
| `itad_checked_at` | UTC timestamp, nullable | When the ITAD job looked the game up, whether or not ITAD knew it. NULL means not looked up yet; failed requests leave it NULL so they are retried. Games that already had ITAD history when the column was added were marked done. |

## itad_games

Maps Steam games to IsThereAnyDeal IDs so history can be fetched without a lookup.

| Column | Type | Source / meaning |
| --- | --- | --- |
| `app_id` | integer, PK, FK → `games` | |
| `itad_id` | text | ITAD game UUID from `games/lookup/v1`. |
| `slug` | text, nullable | ITAD URL slug. |

## sale_calendar

Steam-wide sale events, for features like "days until the next seasonal sale".
Empty for now; nothing fills it yet.

| Column | Type | Source / meaning |
| --- | --- | --- |
| `id` | integer, PK | |
| `name` | text | e.g. `"Steam Summer Sale 2025"`. |
| `starts_on` | date | First day of the event. |
| `ends_on` | date, nullable | Last day of the event. |
| `kind` | text, nullable | e.g. `"seasonal"` or `"fest"`. |

## Migrating the old database

`scripts/migrate_sqlite.py OLD.db` copies the pre-Alembic SQLite file into whatever
`DATABASE_URL` points to (which must be migrated and empty). It opens the old file
read-only and:

- converts float dollars to integer cents (`round(price * 100)`);
- splits comma-separated genres into `genres` and `game_genres`;
- parses release date text into dates;
- turns `price_snapshots` into `steam` rows, dropping snapshots whose price matches the
  game's previous one and snapshots with no price;
- copies `itad_price_history` rows with `shop = "Steam"` into `itad` rows, dropping
  other stores and duplicates;
- creates a `poll_state` row for every game with `next_check_at` set to now.

Known limitation: the old collector stored ITAD timestamps without their UTC offset.
They are treated as UTC, so migrated `itad` times may be off by the offset ITAD used
(likely an hour or two).
