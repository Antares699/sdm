# SDM - SDM Downloads Music

`sdm` is a fast and lightweight CLI tool to download and sync Playlists, Albums, and Tracks from **Spotify, Apple Music, Tidal, YouTube, and SoundCloud**.

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
- `--lyrics`: Embed perfectly synced lyrics.
- `--normalize`: Apply EBU R128 (-14 LUFS) volume normalization.
- `--sponsor-block`: Trim non-music sections from YouTube sources.
- `-w 5`: Download with 5 concurrent workers.
- `--dry-run`: Simulate a sync/download to see what would change.

## Important Notes

**Spotify Playlists**
Spotify playlists must be set to **Public**. Private playlists and personalized mixes (like "Discover Weekly") will not work.

**Age-Restricted Tracks (Cookies)**
If YouTube blocks explicit tracks, pass your browser cookies. Due to App-Bound Encryption in Chrome/Edge, **Firefox is highly recommended**:
```bash
sdm download <URL> --cookies firefox
```
*(Alternatively, use an extension to export `cookies.txt` and pass its path).*
