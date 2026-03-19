# SDM - SDM Downloads Music

`sdm` is a fast, lightweight, and incredibly reliable CLI tool to download and sync Spotify Playlists, Albums, and Tracks directly to your local machine.

## Why sdm?

Recently, Spotify restricted their Web API, requiring developers to have a Premium subscription, which broke popular open-source tools with 403 Forbidden errors.

`sdm` completely bypasses the official API restrictions. It cleverly uses public metadata scraping to extract precise track data (including cover art) and relies on yt-dlp to fetch the highest quality M4A audio available on YouTube. It then tags the files flawlessly.

## Features

* No API Keys Required: Bypasses Spotify's Premium API requirements. Zero rate limits.
* **Universal Support:** Downloads Playlists, Albums, and Tracks from **Spotify, YouTube, YouTube Music, and SoundCloud**.
* **Smart Syncing:** The `--sync` flag mirrors your source folder perfectly, handling deletions cleanly.
* **M3U Generator:** Automatically creates a playable `.m3u` file to preserve the precise Spotify track order.
* **Flawless Metadata:** Embeds precise tags natively: Track, Artist, Cover Art, Track Number, Disc Number, Album Name, Album Artist, Genre, Release Year, and original Spotify URL.
* **Advanced Extras:** Built-in auto-lyrics embedding, LUFS audio normalization, and SponsorBlock trimming.

## Installation

Since sdm is a standard Python package, installation is simple. Just run:

```bash
pip install sdm-pycli
```

**Requirements:**
No additional software is required. `sdm` bundles everything it needs (including `ffmpeg`) during installation.

## Usage

Once installed, the `sdm` command is available globally in your terminal.

### Download
Download a playlist, album, or track to your current directory:
```bash
sdm download "https://open.spotify.com/playlist/YOUR_PLAYLIST_ID"
```

### Custom Output Directory
Use the `-o` or `--output` flag to specify where the songs should be saved:
```bash
sdm download "https://open.spotify.com/album/YOUR_ALBUM_ID" -o "./My Music"
```

### Sync (Two-way mirroring)
If you add or remove songs from your Spotify playlist, run the same command with `--sync`. sdm will instantly download the new songs and delete any local songs that are no longer in the Spotify playlist.

*Bonus:* When downloading or syncing, `sdm` automatically generates a `_playlist.m3u` file. Double-clicking this in VLC, iTunes, or car stereos ensures your tracks play in the **exact custom order** they appear on Spotify!

`sdm` is smart—if you manually inject a track using the `inject` command (see below), it is marked as "protected" in your local `.sync.json` database and will **never be deleted** during a cleanup, even if it's not in the Spotify playlist anymore!
```bash
sdm download "https://open.spotify.com/playlist/YOUR_PLAYLIST_ID" -o "./My Playlist" --sync
```

### Turbo Mode (Concurrent Workers)
Want it faster? Use `-w` or `--workers` to download up to 10 songs at the exact same time:
```bash
sdm download "https://open.spotify.com/playlist/YOUR_PLAYLIST_ID" -o "./My Playlist" -w 5
```

### Advanced Features (Lyrics, Normalization, SponsorBlock)
`sdm` offers advanced flags to give you the ultimate listening experience:
*   `--lyrics`: Automatically fetches perfectly synced lyrics from LRCLIB and embeds them natively into the `.m4a` file. Apple Music and mobile players will display scrolling lyrics effortlessly.
*   `--normalize`: Applies EBU R128 (`-14 LUFS`) audio normalization via `ffmpeg`. This ensures all downloaded (and injected) tracks play at the exact same studio volume level, just like Spotify.
*   `--sponsor-block`: Automatically removes 30-second skits, talking intros, and silence from YouTube Music Videos using the crowdsourced SponsorBlock API.
*   `--refresh-metadata` (or `--refresh`): Forcefully re-tags your existing local files with the latest metadata, cover art, and lyrics without re-downloading the audio. Perfect for cleaning up libraries with missing tags.

```bash
sdm download "https://open.spotify.com/playlist/YOUR_PLAYLIST_ID" -o "./My Playlist" --lyrics --normalize --sponsor-block
```

### Manual Injection (`inject`)
If you have a high-quality local file (e.g., a FLAC or a MP3) and want to weave it into your playlist with official metadata:
```bash
sdm inject "C:\Downloads\my_song.mp3" "https://open.spotify.com/track/..." -o "./My Playlist"
```
`sdm` will:
*   Convert your file to the playlist-standard `.m4a` format automatically via the bundled `ffmpeg`.
*   Fetch and embed the official Spotify metadata and high-res cover art.
*   **Auto-Indexing:** It natively searches your local sync cache to automatically name the file with the correct track number based on your specific playlist order (e.g., `103 - Artist - Title.m4a`). No more typing manual indexes!

### Interactive DRM Fallback
When YouTube blocks an explicit track due to aggressive DRM/App-Bound Encryption, `sdm` doesn't just fail. At the end of your massive syncing run, it groups all the blocked tracks together and provides an **interactive prompt** allowing you to:
*   **Option A:** Automatically fallback to SoundCloud (Quick but potentially lower quality bootlegs).
*   **Option B:** Exit and use the `sdm inject` command to manually add your own high-quality files instead.

### Downloading Explicit or Age-Restricted Songs
YouTube automatically blocks downloaders from accessing age-restricted content (like explicit songs). To bypass this, `sdm` allows you to authenticate using your browser's cookies. 

**Important Note for Chrome/Edge Users:** 
Recent versions of Chrome and Edge use "App-Bound Encryption" which blocks external scripts from reading their cookies. If you try `--cookies chrome`, it will likely fail. Instead, please use one of the two reliable methods below:

**Method 1: The Firefox Method (Recommended & Native)**
Firefox does not encrypt its database. If you use Firefox, simply log into YouTube and run:
```bash
sdm download "https://open.spotify.com/track/EXPLICIT_SONG_ID" --cookies firefox
```

**Method 2: The Text File Method (For Chrome / Edge / Brave)**
If you use Chrome or Edge, you can easily export a text file containing your cookies.
1. Install an open-source extension like "Get cookies.txt LOCALLY" on your browser.
2. Go to YouTube.com, click the extension, and export `cookies.txt` to your folder.
3. Pass the path to the text file using the cookies flag:
```bash
sdm download "https://open.spotify.com/track/EXPLICIT_SONG_ID" --cookies "C:\path\to\your\cookies.txt"
```

