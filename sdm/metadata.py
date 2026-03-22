import re
import json
import requests
import yt_dlp

_session = requests.Session()
_session.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        )
    }
)
_DEFAULT_TIMEOUT = 10


def _fetch_spotify_embed(embed_url):
    html = _session.get(embed_url, timeout=_DEFAULT_TIMEOUT).text
    match = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', html)
    if not match:
        raise ValueError("Could not extract Spotify embed data.")
    data = json.loads(match.group(1))
    entity = (
        data.get("props", {})
        .get("pageProps", {})
        .get("state", {})
        .get("data", {})
        .get("entity")
    )
    if not entity:
        raise ValueError("Could not parse Spotify embed entity.")
    return entity


def _get_embed_cover(entity):
    images = entity.get("visualIdentity", {}).get("image", [])
    if images:
        largest = sorted(images, key=lambda x: x.get("maxHeight", 0), reverse=True)
        return largest[0].get("url", "")
    return ""


def _parse_subtitle_artists(subtitle):
    if not subtitle:
        return ["Unknown Artist"]
    return [a.strip() for a in subtitle.replace("\xa0", " ").split(",")]


def get_playlist_tracks(url):
    match = re.search(r"playlist/([a-zA-Z0-9]+)", url)
    if not match:
        raise ValueError("Invalid Spotify playlist URL")
    playlist_id = match.group(1)

    entity = _fetch_spotify_embed(
        f"https://open.spotify.com/embed/playlist/{playlist_id}"
    )
    playlist_name = entity.get("name", "Spotify Playlist")
    cover_url = _get_embed_cover(entity)

    tracks = []
    for i, item in enumerate(entity.get("trackList", []), start=1):
        uri = item.get("uri", "").replace("spotify:track:", "")
        if not uri:
            continue
        artists = _parse_subtitle_artists(item.get("subtitle"))
        tracks.append(
            {
                "id": f"sp:{uri}",
                "name": item.get("title", "Unknown Track"),
                "artists": artists,
                "album": playlist_name,
                "album_artists": artists,
                "track_number": i,
                "disc_number": 1,
                "duration": item.get("duration"),
                "cover_url": cover_url,
                "source_url": f"https://open.spotify.com/track/{uri}",
            }
        )
    return tracks


def get_album_tracks(url):
    match = re.search(r"album/([a-zA-Z0-9]+)", url)
    if not match:
        raise ValueError("Invalid Spotify album URL")
    album_id = match.group(1)

    entity = _fetch_spotify_embed(f"https://open.spotify.com/embed/album/{album_id}")
    album_name = entity.get("name", "Unknown Album")
    album_artist = entity.get("subtitle", "Unknown Artist")
    album_artists = _parse_subtitle_artists(album_artist)
    cover_url = _get_embed_cover(entity)

    tracks = []
    for i, item in enumerate(entity.get("trackList", []), start=1):
        uri = item.get("uri", "").replace("spotify:track:", "")
        if not uri:
            continue
        artists = _parse_subtitle_artists(item.get("subtitle"))
        tracks.append(
            {
                "id": f"sp:{uri}",
                "name": item.get("title", "Unknown Track"),
                "artists": artists,
                "album": album_name,
                "album_artists": album_artists,
                "track_number": i,
                "disc_number": 1,
                "duration": item.get("duration"),
                "cover_url": cover_url,
                "source_url": f"https://open.spotify.com/track/{uri}",
            }
        )
    return tracks


def get_single_track(url):
    match = re.search(r"track/([a-zA-Z0-9]+)", url)
    if not match:
        raise ValueError("Invalid Spotify track URL")
    track_id = match.group(1)

    entity = _fetch_spotify_embed(f"https://open.spotify.com/embed/track/{track_id}")
    cover_url = _get_embed_cover(entity)

    artists = []
    for a in entity.get("artists", []):
        name = a.get("name")
        if name:
            artists.append(name)
    if not artists:
        artists = _parse_subtitle_artists(entity.get("subtitle"))

    # Scrape the regular page to find the album name
    album_name = "Unknown Album"
    try:
        html = _session.get(url, timeout=_DEFAULT_TIMEOUT).text
        album_match = re.search(
            r'<meta name="music:album" content="https://open\.spotify\.com/album/([^"]+)"',
            html,
        )
        if album_match:
            album_id = album_match.group(1)
            album_entity = _fetch_spotify_embed(
                f"https://open.spotify.com/embed/album/{album_id}"
            )
            album_name = album_entity.get("name", "Unknown Album")
    except Exception:
        pass

    return [
        {
            "id": f"sp:{track_id}",
            "name": entity.get("title") or entity.get("name", "Unknown Track"),
            "artists": artists,
            "album": album_name,
            "album_artists": artists,
            "track_number": 1,
            "disc_number": 1,
            "duration": entity.get("duration"),
            "cover_url": cover_url,
            "source_url": url,
        }
    ]


def get_youtube_tracks(url):
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "extract_flat": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)
        if not info:
            raise ValueError("Could not extract metadata from URL.")

        tracks = []

        if "entries" in info:
            for i, entry in enumerate(info["entries"], start=1):
                if not entry:
                    continue
                tracks.append(
                    {
                        "id": f"yt:{entry.get('id')}",
                        "name": entry.get("title") or "Unknown Track",
                        "artists": [entry.get("uploader") or "Unknown Artist"],
                        "album": info.get("title") or "YouTube Playlist",
                        "album_artists": [info.get("uploader") or "Various Artists"],
                        "track_number": i,
                        "disc_number": 1,
                        "cover_url": entry.get("thumbnail") or "",
                        "source_url": entry.get("url") or entry.get("webpage_url"),
                        "direct_url": entry.get("url") or entry.get("webpage_url"),
                    }
                )
        else:
            tracks.append(
                {
                    "id": f"yt:{info.get('id')}",
                    "name": info.get("title") or "Unknown Track",
                    "artists": [info.get("uploader") or "Unknown Artist"],
                    "album": "YouTube Music",
                    "album_artists": [info.get("uploader") or "Unknown Artist"],
                    "track_number": 1,
                    "disc_number": 1,
                    "cover_url": info.get("thumbnail") or "",
                    "source_url": url,
                    "direct_url": url,
                }
            )
        return tracks


def get_apple_music_tracks(url):
   
    html = _session.get(url, timeout=_DEFAULT_TIMEOUT).text

    album_name = "Apple Music"
    album_artists = ["Unknown Artist"]
    cover_url = ""

    ld_match = re.search(
        r"<script id=schema:music-(album|playlist)[^>]*>([\s\S]*?)</script>", html
    )
    if ld_match:
        try:
            ld_data = json.loads(ld_match.group(2).strip())
            album_name = ld_data.get("name", album_name)

            if "author" in ld_data:
                album_artists = [ld_data["author"].get("name", "Apple Music")]
            elif "byArtist" in ld_data:
                by_artist = ld_data["byArtist"]
                if isinstance(by_artist, list):
                    album_artists = [a.get("name") for a in by_artist if a.get("name")]
                elif isinstance(by_artist, dict):
                    album_artists = [by_artist.get("name", "Unknown Artist")]

            if "image" in ld_data:
                img = ld_data["image"]
                if isinstance(img, str):
                    cover_url = img
                elif isinstance(img, list) and len(img) > 0:
                    cover_url = img[0]
        except Exception:
            pass
    else:
        ld_match = re.search(r"<script id=schema:song[^>]*>([\s\S]*?)</script>", html)
        if ld_match:
            try:
                ld_data = json.loads(ld_match.group(1).strip())

                audio = ld_data.get("audio", {})
                if isinstance(audio, list) and len(audio) > 0:
                    audio = audio[0]
                elif not isinstance(audio, dict):
                    audio = {}

                in_album = audio.get("inAlbum", {})
                if isinstance(in_album, list) and len(in_album) > 0:
                    in_album = in_album[0]

                album_name = in_album.get("name", album_name)

                by_artist = audio.get("byArtist", [])
                if isinstance(by_artist, list):
                    album_artists = [a.get("name") for a in by_artist if a.get("name")]
                elif isinstance(by_artist, dict):
                    album_artists = [by_artist.get("name", "Unknown Artist")]

                if "image" in ld_data:
                    img = ld_data["image"]
                    if isinstance(img, str):
                        cover_url = img
                    elif isinstance(img, list) and len(img) > 0:
                        cover_url = img[0]
            except Exception:
                pass

    match = re.search(r'id="serialized-server-data">([^<]+)</script>', html)
    if not match:
        raise ValueError("Could not extract Apple Music metadata.")

    data = json.loads(match.group(1))
    tracks = []

    for item in data.get("data", []):
        if "data" in item and "sections" in item["data"]:
            for sec in item["data"]["sections"]:
                if "items" in sec:
                    for i, t in enumerate(sec["items"], start=1):
                        content_desc = t.get("contentDescriptor", {})
                        if content_desc.get("kind") == "song":
                            title = t.get("title", "Unknown Track")
                            adam_id = content_desc.get("identifiers", {}).get(
                                "storeAdamID", ""
                            )
                            track_url = content_desc.get("url", url)

                            artists = album_artists
                            sub_links = t.get("subtitleLinks")
                            if (
                                sub_links
                                and isinstance(sub_links, list)
                                and len(sub_links) > 0
                            ):
                                artists = [sub_links[0].get("title", "Unknown Artist")]

                            track_num = t.get("trackNumber", i)

                            tracks.append(
                                {
                                    "id": f"am:{adam_id}",
                                    "name": title,
                                    "artists": artists,
                                    "album": album_name,
                                    "album_artists": album_artists,
                                    "track_number": track_num,
                                    "disc_number": 1,
                                    "cover_url": cover_url,
                                    "source_url": track_url,
                                    "direct_url": None,
                                }
                            )

    if "?i=" in url:
        adam_match = re.search(r"\?i=(\d+)", url)
        if adam_match:
            target_id = adam_match.group(1)
            filtered = [t for t in tracks if target_id in t["id"]]
            if filtered:
                return filtered

    return tracks


def fetch_tracks(url):
    if "spotify.com" in url:
        if "playlist/" in url:
            return get_playlist_tracks(url)
        elif "album/" in url:
            return get_album_tracks(url)
        elif "track/" in url:
            return get_single_track(url)
    elif "music.apple.com" in url:
        return get_apple_music_tracks(url)
    elif (
        "youtube.com" in url
        or "youtu.be" in url
        or "soundcloud.com" in url
        or "tidal.com" in url
    ):
        return get_youtube_tracks(url)

    raise ValueError(
        "URL must be a supported Spotify, Apple Music, Tidal, YouTube, or SoundCloud link."
    )


def get_itunes_metadata(track_name, artist_name):
    try:
        url = "https://itunes.apple.com/search"
        query = f"{artist_name} {track_name}"
        params = {"term": query, "media": "music", "limit": 1}
        res = _session.get(url, params=params, timeout=5).json()
        if res.get("results"):
            t = res["results"][0]
            return t.get("primaryGenreName"), t.get("releaseDate")
    except Exception:
        pass
    return None, None


def get_lrclib_lyrics(track_name, artist_name, album_name=None):
    try:
        url = "https://lrclib.net/api/get"
        params = {"track_name": track_name, "artist_name": artist_name}
        if album_name:
            params["album_name"] = album_name
        res = _session.get(url, params=params, timeout=5).json()
        return res.get("syncedLyrics") or res.get("plainLyrics")
    except Exception:
        pass
    return None
