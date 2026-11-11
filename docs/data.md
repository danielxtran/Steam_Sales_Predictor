# Data model

The schema is defined in `collector/db/models.py` and created by Alembic migrations in
`migrations/` (`alembic upgrade head`). It works on SQLite (local) and Postgres (Neon).

Conventions:

- **Money** is integer cents in US dollars (`1999` = $19.99).
- **Timestamps** (`*_at`) are timezone-aware UTC. On SQLite they are stored as UTC text
  and read back with the UTC timezone attached.
- **Dates** (`release_date`, `starts_on`, `ends_on`) are plain calendar dates.

Sources:

- **Steam** — `store.steampowered.com/api/appdetails?cc=us` (`collector/sources/steam.py`).
- **ITAD** — IsThereAnyDeal API, `games/lookup/v1` and `games/history/v2?country=US`
  (`collector/sources/itad.py`).
- **Old DB** — rows copied from the pre-Alembic SQLite file by `scripts/migrate_sqlite.py`.

## games

One row per Steam game. Updated every time the collector fetches the game.

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
| `type` | text | Steam app `type`. Always `"game"` today; the collector skips DLC, demos, etc. |
| `created_at` | UTC timestamp | When we first stored the game. Migrated rows use their first old price snapshot time. |
| `updated_at` | UTC timestamp | Last time the metadata was refreshed. Migrated rows use the old `last_updated`. |

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

The price time series. `steam` rows are written only when the price differs from the
game's previous `steam` row; `itad` rows are ITAD's own list of price changes. Unique on `(app_id, observed_at, source)`, so
re-running a job cannot create duplicates. Indexed on `(app_id, observed_at)`.

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

One row per game: when it was last checked and when it is due next. Every successful
fetch updates `last_checked_at`, even when the price didn't change.

| Column | Type | Source / meaning |
| --- | --- | --- |
| `app_id` | integer, PK, FK → `games` | |
| `tier` | text | `"hot"` (poll often) or `"normal"`. Everything is `"normal"` for now; tiering rules come later. |
| `last_checked_at` | UTC timestamp, nullable | Last time the collector fetched this game. |
| `next_check_at` | UTC timestamp, nullable | When the game is due. The migration sets it to the migration time, so every game is due. Not yet used by the collector. |
| `last_error` | text, nullable | Error from the last failed check; cleared on success. |

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
