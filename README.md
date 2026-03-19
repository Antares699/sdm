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
*Note: `sdm` comes with everything bundled, including FFmpeg.*

## Quick Start

### 1. Download
Download a playlist, album, or track to your current directory. Use `-f` to specify the format.
```bash
sdm download "https://music.apple.com/us/album/..." -f flac -o "./My Music"
```

### 2. Sync
`sdm` remembers the URL. Later, simply run `sync` to update the folder with the newest changes from the remote playlist:
```bash
sdm sync "./My Music"
```

### 3. Inject Local Files
Have your own pristine audio file? Weave it seamlessly into your playlist with official metadata. It will be protected from future sync deletions:
```bash
sdm inject "song.mp3" "https://open.spotify.com/track/..." -o "./My Music"
```

## Advanced Usage

Combine flags for the ultimate listening experience:
```bash
sdm download <URL> --lyrics --normalize --sponsor-block -w 5
```
- `--lyrics`: Embed perfectly synced lyrics for Apple Music / mobile players.
- `--normalize`: Apply EBU R128 (-14 LUFS) volume normalization.
- `--sponsor-block`: Trim non-music sections from YouTube sources.
- `-w 5`: Download with 5 concurrent workers.
- `--dry-run`: Simulate a sync/download to see what would change.

## Music Sourcing & Audio Quality

`sdm` fetches flawless metadata (tags, cover art, tracklists) directly from Spotify, Apple Music, and Tidal, but sources the actual audio streams from YouTube. 

Unlike other tools that require your Premium credentials and risk permanent account bans by ripping directly from encrypted servers, `sdm` acts as a safe, unauthenticated metadata matcher. It reliably delivers DRM-free audio (up to 256kbps AAC or Opus) perfectly packaged to match the original release.

## Legal Disclaimer

*Users are responsible for their actions. We do not support unauthorized downloading of copyrighted material. `sdm` is open-source (MIT License) for educational and personal use only.*

---

**⚠️ Important Technical Notes:**
- **Spotify Playlists:** Must be set to **Public**. Private playlists and personalized mixes (like "Discover Weekly") will not work.
- **Explicit Content:** If YouTube blocks downloads, pass browser cookies using `--cookies firefox`. Firefox is recommended as it does not encrypt the cookie database.
