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

```bash
pip install sdm-pycli
```

No additional software is required. `sdm` bundles everything it needs, including FFmpeg.

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

## Legal Disclaimer

*Users are responsible for their actions. We do not support unauthorized downloading of copyrighted material. `sdm` is open-source (MIT License) for educational and personal use only.*

---

**Important Notes:**
- **Spotify Playlists** must be set to **Public**. Private playlists and personalized mixes will not work.
- **Explicit Content:** If YouTube blocks downloads, pass browser cookies with `--cookies firefox`. Firefox is recommended.
