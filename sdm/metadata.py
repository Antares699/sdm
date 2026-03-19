import re
import json
import requests
import spotapi
import yt_dlp


def _parse_track_dict(t3, t2):
    name = t3.get("identityTrait", {}).get("name")
    if not name:
        name = t3.get("name") or t2.get("name")

    artists = []
    contributors = t3.get("identityTrait", {}).get("contributors", {}).get("items", [])
    for a in contributors:
        artist_name = a.get("profile", {}).get("name") or a.get("name")
        if artist_name:
            artists.append(artist_name)

    track_number = t2.get("trackNumber", 0)
    disc_number = t2.get("discNumber", 0)
    uri = t2.get("uri", "").replace("spotify:track:", "") or t3.get("uri", "").replace(
        "spotify:track:", ""
    )

    cover_url = ""
    album_data = t2.get("albumOfTrack", {})
    album_name = album_data.get("name", "")
    album_artists = []
    for a in album_data.get("artists", {}).get("items", []):
        aa_name = a.get("profile", {}).get("name") or a.get("name")
        if aa_name:
            album_artists.append(aa_name)

    cover_sources = album_data.get("coverArt", {}).get("sources", [])
    if cover_sources:
        cover_sources.sort(key=lambda x: x.get("width", 0), reverse=True)
        cover_url = cover_sources[0].get("url")

    if not uri:
        return None

    return {
        "id": f"sp:{uri}",
        "name": name,
        "artists": artists,
        "album": album_name,
        "album_artists": album_artists,
        "track_number": track_number,
        "disc_number": disc_number,
        "cover_url": cover_url,
        "source_url": f"https://open.spotify.com/track/{uri}",
    }


def get_playlist_tracks(url):
    match = re.search(r"playlist/([a-zA-Z0-9]+)", url)
    if not match:
        raise ValueError("Invalid Spotify playlist URL")
    playlist_id = match.group(1)

    tracks = []
    for chunk in spotapi.PublicPlaylist(playlist_id).paginate_playlist():
        items = chunk.get("items", [])
        for item in items:
            t3 = item.get("itemV3", {}).get("data", {})
            t2 = item.get("itemV2", {}).get("data", {})
            if t3 and t2:
                track = _parse_track_dict(t3, t2)
                if track:
                    tracks.append(track)
    return tracks


def get_album_tracks(url):
    match = re.search(r"album/([a-zA-Z0-9]+)", url)
    if not match:
        raise ValueError("Invalid Spotify album URL")
    album_id = match.group(1)

    album = spotapi.PublicAlbum(album_id)
    album_data = album.get_album_info().get("data", {}).get("albumUnion", {})
    album_name = album_data.get("name", "")
    album_artists = [
        a.get("profile", {}).get("name")
        for a in album_data.get("artists", {}).get("items", [])
        if a.get("profile", {}).get("name")
    ]
    album_cover_url = ""
    try:
        sources = album_data.get("coverArt", {}).get("sources", [])
        if sources:
            sources.sort(key=lambda x: x.get("width", 0), reverse=True)
            album_cover_url = sources[0].get("url")
    except Exception:
        pass

    tracks = []
    for chunk in album.paginate_album():
        for item in chunk:
            track_data = item.get("track", {})
            if not track_data:
                continue

            uri = track_data.get("uri", "").replace("spotify:track:", "")
            if not uri:
                continue

            artists = [
                a.get("profile", {}).get("name")
                for a in track_data.get("artists", {}).get("items", [])
                if a.get("profile", {}).get("name")
            ]

            tracks.append(
                {
                    "id": f"sp:{uri}",
                    "name": track_data.get("name"),
                    "artists": artists,
                    "album": album_name,
                    "album_artists": album_artists,
                    "track_number": track_data.get("trackNumber", 0),
                    "disc_number": track_data.get("discNumber", 0),
                    "cover_url": album_cover_url,
                    "source_url": f"https://open.spotify.com/track/{uri}",
                }
            )
    return tracks


def get_single_track(url):
    match = re.search(r"track/([a-zA-Z0-9]+)", url)
    if not match:
        raise ValueError("Invalid Spotify track URL")
    track_id = match.group(1)

    html = requests.get(url).text

    title = re.search(r'<meta name="twitter:title" content="([^"]+)"', html)
    artist_matches = re.finditer(
        r'<meta name="music:musician_description" content="([^"]+)"', html
    )
    image = re.search(r'<meta name="twitter:image" content="([^"]+)"', html)

    album_match = re.search(
        r'<meta name="music:album" content="https://open\.spotify\.com/album/([^"]+)"',
        html,
    )
    album_name = "Unknown Album"
    if album_match:
        try:
            album_id = album_match.group(1)
            album = spotapi.PublicAlbum(album_id)
            a_data = album.get_album_info().get("data", {}).get("albumUnion", {})
            album_name = a_data.get("name", "Unknown Album")
        except Exception:
            pass

    artist_names = []
    for m in artist_matches:
        parts = [a.strip() for a in m.group(1).split(",")]
        artist_names.extend(parts)

    if not title:
        raise ValueError("Could not extract track metadata from the page HTML.")

    return [
        {
            "id": f"sp:{track_id}",
            "name": title.group(1),
            "artists": artist_names,
            "album": album_name,
            "album_artists": artist_names,
            "track_number": 1,
            "disc_number": 1,
            "cover_url": image.group(1) if image else "",
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
            raise ValueError("Could not extract metadata from YouTube URL.")

        tracks = []

        if "entries" in info:
            # It's a playlist or album
            for i, entry in enumerate(info["entries"], start=1):
                if not entry:
                    continue
                tracks.append(
                    {
                        "id": f"yt:{entry.get('id')}",
                        "name": entry.get("title") or "Unknown YouTube Track",
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
            # It's a single video
            tracks.append(
                {
                    "id": f"yt:{info.get('id')}",
                    "name": info.get("title") or "Unknown YouTube Track",
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
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
    }
    html = requests.get(url, headers=headers).text

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
                author = ld_data["author"]
                album_artists = [author.get("name", "Apple Music")]
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
        res = requests.get(url, params=params, timeout=5).json()
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
        res = requests.get(url, params=params, timeout=5).json()
        return res.get("syncedLyrics") or res.get("plainLyrics")
    except Exception:
        pass
    return None
