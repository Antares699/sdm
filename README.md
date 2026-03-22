# SDM - SDM Downloads Music

`sdm` is a fast, lightweight, and zero-config CLI tool to download and sync Playlists, Albums, and Tracks from **Spotify, Apple Music, Tidal, YouTube, and SoundCloud**.

## Why sdm?

Recently, Spotify restricted their Web API, requiring developers to have a Premium subscription, which broke popular open-source tools with `403 Forbidden` errors. 

`sdm` was built to completely bypass these restrictions. It uses native web scraping to extract flawless metadata directly from streaming services, pairs it with `yt-dlp` to fetch the highest-quality audio, and embeds the tags natively. No API keys required.

## Features

- **Universal Support:** Spotify, Apple Music, Tidal, YouTube, and SoundCloud.
- **Smart Syncing:** Two-way mirroring. Run `sdm sync` to fetch new tracks and clean up removed ones.
- **Multi-Format:** Download in M4A (default), MP3, FLAC, or OPUS with native tags.
- **Perfect Metadata:** Embeds Track, Artist, Album, Cover Art, Track/Disc Numbers, Genre, and Release Year.
- **Advanced Processing:** Synced lyrics via LRCLIB, LUFS normalization, and SponsorBlock trimming.

## Installation

You must have [Python 3.8+](https://www.python.org/downloads/) installed on your system. 
*(Windows users: Make sure to check the "Add Python to PATH" box during installation!)*

Install directly from PyPI:

```bash
pip install sdm-pycli
```

## Usage

Open a terminal in the folder where you want your music saved, then run the commands below.

### Playlist

To download a playlist, run

```bash
sdm download [playlistUrl]
```

Example:

```bash
sdm download https://open.spotify.com/playlist/37i9dQZF1E8NjgPSXnmGkI?si=O0kgUaFcQnatcvlZzS9yJw
```

### Album

To download an album, run

```bash
sdm download [albumUrl]
```

Example:

```bash
sdm download https://open.spotify.com/album/6eUW0wxWtzkFdaEFsTJto6?si=nYD6g_tZQvuFsMzyKG2sRA
```

### Track

To download a single track, run

```bash
sdm download [trackUrl]
```

Example:

```bash
sdm download https://open.spotify.com/track/4PTG3Z6ehGkBFwjybzWkR8?si=1d83934b63464e9e
```

### Apple Music

Apple Music albums, playlists, and songs are also supported.

```bash
sdm download https://music.apple.com/us/album/starboy/1440871397
```

### Sync

`sdm` remembers the source URL for each folder. To update a previously downloaded folder with any changes from the remote playlist, run

```bash
sdm sync [directory]
```

Example:

```bash
sdm sync "C:\Users\You\Music\My Playlist"
```

### Inject

To import a local audio file into your playlist with official metadata and protect it from future sync deletions, run

```bash
sdm inject [filePath] [trackUrl]
```

Example:

```bash
sdm inject "C:\Users\You\Downloads\song.mp3" https://open.spotify.com/track/0VjIjW4GlUZAMYd2vXMi3b
```

## Options & Flags

| Flag | Description |
|---|---|
| `-o, --output` | Save to a specific directory |
| `-f, --format` | Audio format: `m4a`, `mp3`, `flac`, `opus` (default: `m4a`) |
| `-w, --workers` | Number of concurrent downloads (default: `3`) |
| `--lyrics` | Fetch and embed synced lyrics from LRCLIB |
| `--normalize` | Apply EBU R128 (-14 LUFS) volume normalization |
| `--sponsor-block` | Trim non-music sections from YouTube sources |
| `--dry-run` | Simulate a sync/download without making changes |
| `--no-delete` | Download new tracks but never delete local files |
| `--refresh-metadata` | Re-tag existing files with the latest metadata |
| `--cookies` | Pass browser cookies for age-restricted content |

Example with flags:

```bash
sdm download https://open.spotify.com/playlist/37i9dQZF1E8UXBoz02kGID -f flac --lyrics --normalize
```

## Music Sourcing & Audio Quality

`sdm` fetches flawless metadata (tags, cover art, tracklists) directly from Spotify, Apple Music, and Tidal, but sources the actual audio streams from YouTube. 

Unlike other tools that require your Premium credentials and risk permanent account bans by ripping directly from encrypted servers, `sdm` acts as a safe, unauthenticated metadata matcher. It reliably delivers DRM-free audio (up to 256kbps AAC or Opus) perfectly packaged to match the original release.

## Optimization

`sdm` v1.2.0 implements nine optimization techniques across five categories to significantly reduce network overhead, disk I/O, startup time, and download accuracy.

### 1. Application-Level Caching

**Problem:** Every track triggered its own HTTP requests for cover art and iTunes metadata, even when all tracks in an album share the same cover image and genre.

**Fix:** Thread-safe in-memory caches (`_cover_cache`, `_itunes_cache`) store results keyed by URL and `(artist, track)` respectively. A cache hit skips the HTTP request entirely.

| Metric | Before | After |
|---|---|---|
| Cover art downloads (14-track album) | 14 HTTP requests | 1 HTTP request |
| iTunes metadata lookups (14-track album) | 14 HTTP requests | ~1-2 HTTP requests |
| Total HTTP requests (14 tracks, lyrics enabled) | ~42 | ~16 |

### 2. Efficient Algorithms

**A) SQLite sync storage** — Sync state was stored in a flat `.sync.json` file. Every `mark_synced()` call serialized the entire dictionary to disk. SQLite replaces this with atomic per-row `INSERT OR REPLACE` operations, indexed lookups, and WAL journal mode for crash safety. Legacy `.sync.json` files are auto-migrated on first run.

| Metric | Before (JSON) | After (SQLite) |
|---|---|---|
| Write cost per track | O(n) full serialization | O(1) single row INSERT |
| Total write cost (500 tracks) | O(n^2) | O(n) |
| Crash safety | Data loss if killed mid-write | WAL mode, zero data loss |
| Startup load (10,000 tracks) | Parse entire JSON into memory | No upfront load |
| New dependencies | — | None (sqlite3 is stdlib) |

**B) Smart YouTube match scoring** — Instead of blindly trying the first 3 YouTube search results in order, each result is scored against the target track's metadata (duration match, title similarity, artist/channel match, keyword analysis). Results are sorted by score so the best match is downloaded first.

| Metric | Before | After |
|---|---|---|
| Correct track on first attempt | ~60-70% | ~90%+ |
| Wrong track downloaded (remix/bootleg) | Common | Rare |
| Average download attempts per track | ~1.5 | ~1.1 |

**C) Fixed duplicate FFmpeg postprocessor** — When `--normalize` was enabled, a second identical `FFmpegExtractAudio` postprocessor was appended, causing yt-dlp to transcode the audio twice per track. Removed the duplicate; normalization is correctly applied via `postprocessor_args`.

| Metric | Before | After |
|---|---|---|
| FFmpeg transcodes per track (`--normalize`) | 2 (double transcode) | 1 |

**D) Cached FFmpeg path** — `imageio_ffmpeg.get_ffmpeg_exe()` performs a filesystem scan to locate the ffmpeg binary. Previously called once per track. Now called once and cached globally.

| Metric | Before | After |
|---|---|---|
| Filesystem lookups (50-track playlist) | 50 | 1 |

### 3. HTTP Connection Pooling (`requests.Session`)

**Problem:** Every `requests.get()` call created a new TCP connection with a full TCP/TLS handshake. No HTTP keep-alive, no connection reuse.

**Fix:** A shared `requests.Session()` is used across all HTTP calls (Spotify embed scraping, Apple Music scraping, cover art downloads, iTunes API, LrcLib lyrics). This enables HTTP keep-alive and connection pooling. A default 10-second timeout was also added to all requests that previously had none, preventing the CLI from hanging on unresponsive servers.

| Metric | Before | After |
|---|---|---|
| TCP handshakes (50 tracks, lyrics) | ~150+ | ~4-5 (one per unique host) |
| Requests without timeout | 3 code paths | 0 |

### 4. Lazy Loading

**A) Deferred CLI imports** — `subprocess` and `imageio_ffmpeg` were imported at module level in the CLI entry point, adding startup overhead to every command — even though they are only used by the `inject` command. These imports are deferred to inside the function body.

**B) Lazy-loaded mutagen submodules** — All 5 mutagen submodules (`mutagen.mp4`, `mutagen.mp3`, `mutagen.id3`, `mutagen.flac`, `mutagen.oggopus`) were imported at module level, even though only one format is used per run. Now each submodule is imported inside its format-specific branch of `embed_metadata()`. A `--format flac` run never loads the MP4 atom parser or the 11 ID3 frame classes.

| Metric | Before | After |
|---|---|---|
| Mutagen submodules loaded (`--format flac`) | 5 submodules (17 classes) | 2 submodules (FLAC + Picture) |
| Modules loaded on `sdm download` | `subprocess` + `imageio_ffmpeg` | Not loaded |

### 5. Minimize Dependencies / Reduce Overhead

**A) System FFmpeg detection** — `_get_ffmpeg_path()` now checks `shutil.which("ffmpeg")` first. Users who already have ffmpeg installed system-wide skip the bundled `imageio-ffmpeg` binary entirely, getting a faster PATH lookup. New users without system ffmpeg see zero difference — the bundled binary kicks in automatically as a fallback.

| Metric | Before | After |
|---|---|---|
| FFmpeg resolution (system ffmpeg installed) | Filesystem scan via imageio_ffmpeg | `shutil.which()` PATH lookup |
| Install experience (no system ffmpeg) | Unchanged | Unchanged (bundled fallback) |

**B) Reuse yt-dlp instances per worker** — `YoutubeDL.__init__()` compiles format selectors, loads the extractor registry, and sets up the downloader framework. Previously, a fresh instance was created for every single track. Now one instance is created per worker thread and reused across all tracks assigned to that worker.

| Metric | Before (50-track album, 3 workers) | After |
|---|---|---|
| `YoutubeDL()` instantiations | 50 | 3 |
| Format selector compilations | 50 | 3 |
| Postprocessor setup cycles | 50 | 3 |

## Legal Disclaimer

*Users are responsible for their actions. We do not support unauthorized downloading of copyrighted material. `sdm` is open-source (MIT License) for educational and personal use only.*

---

**Important Notes:**
- **Spotify Playlists** must be set to **Public**. Private playlists and personalized mixes will not work.
- **Explicit Content:** If YouTube blocks downloads, pass browser cookies with `--cookies firefox`. Firefox is recommended.
