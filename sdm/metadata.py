import re
import json
import time
import requests
import threading
import spotapi
import spotapi.client

_orig_hash = spotapi.client.BaseClient.get_sha256_hash
_hash_lock = threading.Lock()
_global_hashes = None


def _fast_hash(self):
    global _global_hashes
    with _hash_lock:
        if _global_hashes is None:
            _orig_hash(self)
            _global_hashes = self.raw_hashes
        else:
            self.raw_hashes = _global_hashes


spotapi.client.BaseClient.get_sha256_hash = _fast_hash

_orig_auth = spotapi.client.BaseClient._auth_rule
_auth_lock = threading.Lock()
_global_auth_state = None


def _fast_auth_rule(self, kwargs: dict) -> dict:
    global _global_auth_state
    with _auth_lock:
        if _global_auth_state is None:
            res = _orig_auth(self, kwargs)
            _global_auth_state = {
                "client_token": getattr(self, "client_token", None),
                "access_token": getattr(self, "access_token", None),
                "client_version": getattr(self, "client_version", None),
                "device_id": getattr(self, "device_id", None),
            }
            return res
        else:
            self.client_token = _global_auth_state["client_token"]
            self.access_token = _global_auth_state["access_token"]
            self.client_version = _global_auth_state["client_version"]
            self.device_id = _global_auth_state["device_id"]
            return _orig_auth(self, kwargs)


spotapi.client.BaseClient._auth_rule = _fast_auth_rule


import yt_dlp
from threading import Lock
import base64
from pathlib import Path
from typing import Any

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

_CRAWLER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; Googlebot/2.1; +http://www.google.com/bot.html)"
}

_token_lock = Lock()
_cached_token = None
_token_expiry = 0


def _fetch_spotify_embed(embed_url):
    html = _session.get(embed_url, timeout=_DEFAULT_TIMEOUT).content.decode("utf-8")
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


_album_cache = {}
_album_cache_lock = Lock()


def _scrape_track_page(track_id):
    result = {
        "album": "Unknown Album",
        "album_artists": [],
        "track_number": 0,
        "disc_number": 1,
        "tracks_count": 0,
        "cover_url": "",
        "release_date": None,
        "isrc": None,
        "genres": [],
    }

    try:
        url = f"https://open.spotify.com/track/{track_id}"
        html = _session.get(
            url, timeout=_DEFAULT_TIMEOUT, headers=_CRAWLER_HEADERS
        ).content.decode("utf-8")
    except Exception:
        return result

    track_num_match = re.search(r'<meta name="music:album:track" content="(\d+)"', html)
    if track_num_match:
        result["track_number"] = int(track_num_match.group(1))

    date_match = re.search(r'<meta name="music:release_date" content="([^"]+)"', html)
    if date_match:
        result["release_date"] = date_match.group(1)

    og_image = re.search(r'<meta property="og:image" content="([^"]+)"', html)
    if og_image:
        result["cover_url"] = og_image.group(1)

    album_match = re.search(
        r'<meta name="music:album" content="https://open\.spotify\.com/album/([^"]+)"',
        html,
    )
    if not album_match:
        return result

    album_id = album_match.group(1)

    with _album_cache_lock:
        cached = _album_cache.get(album_id)

    if cached:
        result["album"] = cached["album"]
        result["album_artists"] = cached["album_artists"]
        result["tracks_count"] = cached["tracks_count"]
        if not result["cover_url"]:
            result["cover_url"] = cached["cover_url"]
        return result

    try:
        entity = _fetch_spotify_embed(
            f"https://open.spotify.com/embed/album/{album_id}"
        )
        album_name = entity.get("name", "Unknown Album")
        subtitle = entity.get("subtitle", "")
        album_artists = (
            [a.strip() for a in subtitle.replace("\xa0", " ").split(",")]
            if subtitle
            else []
        )
        cover_url = _get_embed_cover(entity)
        tracks_count = len(entity.get("trackList", []))

        cache_entry = {
            "album": album_name,
            "album_artists": album_artists,
            "tracks_count": tracks_count,
            "cover_url": cover_url,
        }
        with _album_cache_lock:
            _album_cache[album_id] = cache_entry

        result["album"] = album_name
        result["album_artists"] = album_artists
        result["tracks_count"] = tracks_count
        if not result["cover_url"]:
            result["cover_url"] = cover_url
    except Exception:
        pass

    return result


def get_lastfm_metadata(artist_name, track_name, api_key):
    genres = []
    wiki = None
    mbid = None
    junk_words = {
        "favorite",
        "favourite",
        "love",
        "loved",
        "best",
        "live",
        "amazing",
        "awesome",
        "sex",
        "orgasm",
        "fuck",
        "shit",
        "good",
        "great",
        "sad",
        "happy",
        "melancholy",
        "chill",
        "workout",
        "sleep",
        "seen live",
        "music",
        "song",
    }

    try:
        url = "http://ws.audioscrobbler.com/2.0/"
        params = {
            "method": "track.getinfo",
            "artist": artist_name,
            "track": track_name,
            "api_key": api_key,
            "format": "json",
            "autocorrect": 1,
        }
        res = _session.get(url, params=params, timeout=5).json()

        if "track" in res:
            t = res["track"]
            wiki = t.get("wiki", {}).get("summary")
            if wiki:
                import re

                wiki = re.sub(r"<a href=.*?>.*?</a>", "", wiki).strip()
            mbid = t.get("mbid")

            tags = t.get("toptags", {}).get("tag", [])
            if isinstance(tags, dict):
                tags = [tags]

            for tag in tags:
                name = tag.get("name", "").strip()
                lower_name = name.lower()
                if len(lower_name) <= 2:
                    continue
                if lower_name in artist_name.lower():
                    continue
                if lower_name in track_name.lower():
                    continue
                if any(w in lower_name for w in junk_words):
                    continue
                import re

                if re.match(r"^\d{2,4}s?$", lower_name):
                    continue
                genres.append(name.title())
                if len(genres) >= 5:
                    break

        if not genres:
            params = {
                "method": "artist.gettoptags",
                "artist": artist_name,
                "api_key": api_key,
                "format": "json",
                "autocorrect": 1,
            }
            res_artist = _session.get(url, params=params, timeout=5).json()
            if "toptags" in res_artist and "tag" in res_artist["toptags"]:
                tags = res_artist["toptags"]["tag"]
                if isinstance(tags, dict):
                    tags = [tags]
                for tag in tags:
                    name = tag.get("name", "").strip()
                    lower_name = name.lower()
                    if len(lower_name) <= 2:
                        continue
                    if lower_name in artist_name.lower():
                        continue
                    if any(w in lower_name for w in junk_words):
                        continue
                    import re

                    if re.match(r"^\d{2,4}s?$", lower_name):
                        continue
                    genres.append(name.title())
                    if len(genres) >= 5:
                        break

        return genres, wiki, mbid
    except Exception:
        return genres, wiki, mbid


def _enrich_metadata(base_tracks):
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import time
    import spotapi

    if all(t.get("_enriched") for t in base_tracks):
        return base_tracks

    albums_to_fetch = {}
    tracks_without_album = []

    for track in base_tracks:
        if track.get("_enriched"):
            continue
        album_uri = track.get("_album_uri", "")
        if album_uri and "album:" in album_uri:
            album_id = album_uri.split("album:")[-1]
            albums_to_fetch.setdefault(album_id, []).append(track)
        else:
            tracks_without_album.append(track)

    def fetch_album_metadata(album_id, trks):
        for _ in range(3):
            try:
                a = spotapi.PublicAlbum(album_id)
                info = a.get_album_info()
                return album_id, trks, info
            except Exception:
                time.sleep(1)
        return album_id, trks, None

    def fetch_track_metadata(trk):
        for _ in range(3):
            try:
                tid = trk["id"].replace("sp:", "")
                data = spotapi.Public.song_info(tid)
                return trk, data
            except Exception:
                time.sleep(1)
        return trk, None

    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_album = {
            executor.submit(fetch_album_metadata, aid, trks): aid
            for aid, trks in albums_to_fetch.items()
        }
        for future in as_completed(future_to_album):
            aid, trks, info = future.result()
            if info:
                try:
                    album_data = info.get("data", {}).get("albumUnion", {})
                    if album_data.get("__typename") == "NotFound":
                        raise ValueError("Album not found via spotapi")
                    cover_sources = album_data.get("coverArt", {}).get("sources", [])
                    cover_url = (
                        max(cover_sources, key=lambda x: x.get("height", 0)).get(
                            "url", ""
                        )
                        if cover_sources
                        else ""
                    )
                    date_data = album_data.get("date", {})
                    release_date = date_data.get("isoString") or date_data.get("year")
                    if release_date:
                        release_date = str(release_date).split("T")[0]

                    tracks_v2_obj = album_data.get("tracksV2") or {}
                    tracks_v2 = tracks_v2_obj.get("items", [])
                    total_tracks = tracks_v2_obj.get("totalCount") or len(tracks_v2)

                    if not tracks_v2:
                        raise ValueError("No tracks found in album payload")

                    meta_map = {}
                    for item in tracks_v2:
                        t_data = item.get("track", {})
                        t_uri = t_data.get("uri", "").replace("spotify:track:", "")
                        if t_uri:
                            meta_map[t_uri] = {
                                "track_number": t_data.get("trackNumber"),
                                "disc_number": t_data.get("discNumber"),
                            }

                    for t in trks:
                        tid = t["id"].replace("sp:", "")
                        t["cover_url"] = cover_url or t.get("cover_url", "")
                        t["release_date"] = release_date or t.get("release_date")
                        t["tracks_count"] = total_tracks or t.get("tracks_count", 0)
                        if tid in meta_map:
                            t["track_number"] = meta_map[tid]["track_number"] or t.get(
                                "track_number"
                            )
                            t["disc_number"] = meta_map[tid]["disc_number"] or t.get(
                                "disc_number"
                            )
                        t["_enriched"] = True
                except Exception:
                    tracks_without_album.extend(trks)
            else:
                tracks_without_album.extend(trks)

        future_to_track = {
            executor.submit(fetch_track_metadata, trk): trk
            for trk in tracks_without_album
        }
        for future in as_completed(future_to_track):
            trk, data = future.result()
            if data:
                try:
                    t_data = data.get("data", {}).get("trackUnion", {})
                    if t_data.get("__typename") == "NotFound" or not t_data:
                        raise ValueError("Track not found via spotapi")
                    album_data = t_data.get("albumOfTrack", {}) or t_data.get(
                        "album", {}
                    )
                    tracks_v2_obj = album_data.get("tracksV2") or {}
                    total_tracks = tracks_v2_obj.get("totalCount") or 0

                    cover_sources = album_data.get("coverArt", {}).get("sources", [])
                    cover_url = (
                        max(cover_sources, key=lambda x: x.get("height", 0)).get(
                            "url", ""
                        )
                        if cover_sources
                        else ""
                    )

                    date_data = album_data.get("date", {})
                    release_date = date_data.get("isoString") or date_data.get("year")
                    if release_date:
                        release_date = str(release_date).split("T")[0]

                    trk["cover_url"] = cover_url or trk.get("cover_url", "")
                    trk["release_date"] = release_date or trk.get("release_date")
                    trk["tracks_count"] = total_tracks or trk.get("tracks_count", 0)
                    trk["track_number"] = t_data.get("trackNumber") or trk.get(
                        "track_number"
                    )

                    trk["disc_number"] = t_data.get("discNumber") or trk.get(
                        "disc_number"
                    )
                    trk["_enriched"] = True
                except Exception:
                    pass

            if not trk.get("_enriched"):
                tid = trk["id"].replace("sp:", "")
                info = _scrape_track_page(tid)
                trk["album"] = info.get("album") or trk.get("album")
                trk["album_artists"] = info.get("album_artists") or trk.get(
                    "album_artists", []
                )
                trk["track_number"] = info.get("track_number") or trk.get(
                    "track_number"
                )
                trk["disc_number"] = info.get("disc_number") or trk.get("disc_number")
                trk["tracks_count"] = info.get("tracks_count") or trk.get(
                    "tracks_count"
                )
                trk["cover_url"] = info.get("cover_url") or trk.get("cover_url", "")
                trk["release_date"] = info.get("release_date") or trk.get(
                    "release_date"
                )
                trk["isrc"] = info.get("isrc") or trk.get("isrc")
                trk["genres"] = info.get("genres", []) or trk.get("genres", [])
                trk["_enriched"] = True

    from pathlib import Path
    import json
    import re

    config_file = Path.home() / ".sdm_config.json"
    lastfm_key = None
    if config_file.exists():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                lastfm_key = json.load(f).get("lastfm_key")
        except Exception:
            pass

    if lastfm_key:

        def fetch_lfm(t):
            if t.get("_lfm_enriched"):
                return t, [], None, None
            artist = t.get("artists", [""])[0] if t.get("artists") else ""
            name = t.get("name", "")
            if not artist or not name:
                return t, [], None, None

            clean_name = re.sub(
                r"[\(\[].*?(feat|ft|remaster|radio|edit|mix).*?[\)\]]",
                "",
                name,
                flags=re.I,
            ).strip()
            genres, wiki, mbid = get_lastfm_metadata(artist, clean_name, lastfm_key)
            return t, genres, wiki, mbid

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_track = {executor.submit(fetch_lfm, t): t for t in base_tracks}
            for future in as_completed(future_to_track):
                t, genres, wiki, mbid = future.result()
                if genres:
                    t["genres"] = genres
                if wiki:
                    t["wiki"] = wiki
                if mbid:
                    t["mbid"] = mbid
                t["_lfm_enriched"] = True

    return base_tracks


def _parse_spotapi_track_list(raw_data):
    tracks = []
    for i, item in enumerate(raw_data, start=1):
        t = item.get("itemV2", {}).get("data", {}) or item.get("track", item)
        if not t:
            continue
        track_id = t.get("uri", "").replace("spotify:track:", "") or t.get("id", "")
        if not track_id or "spotify:local:" in t.get("uri", ""):
            continue
        name = t.get("name", "Unknown Track")

        artists = []
        if (
            "artists" in t
            and isinstance(t["artists"], dict)
            and "items" in t["artists"]
        ):
            artists = [
                a.get("profile", {}).get("name", "") for a in t["artists"]["items"]
            ]
        elif "artists" in t and isinstance(t["artists"], list):
            artists = [
                a.get("name", "") or a.get("profile", {}).get("name", "")
                for a in t["artists"]
            ]
        elif "firstArtist" in t:
            first_items = t.get("firstArtist", {}).get("items", [])
            other_items = t.get("otherArtists", {}).get("items", [])
            artists = [
                a.get("profile", {}).get("name", "") for a in first_items + other_items
            ]

        album_data = t.get("albumOfTrack", {}) or t.get("album", {})
        album_name = album_data.get("name", "Unknown Album")

        album_artists = []
        if "artists" in album_data:
            if (
                isinstance(album_data["artists"], dict)
                and "items" in album_data["artists"]
            ):
                album_artists = [
                    a.get("profile", {}).get("name", "")
                    for a in album_data["artists"]["items"]
                ]
            elif isinstance(album_data["artists"], list):
                album_artists = [
                    a.get("name", "") or a.get("profile", {}).get("name", "")
                    for a in album_data["artists"]
                ]
        album_artists = [a for a in album_artists if a] or artists or ["Unknown Artist"]

        cover_url = ""
        if "coverArt" in album_data and "sources" in album_data["coverArt"]:
            sources = album_data["coverArt"]["sources"]
            if sources:
                cover_url = max(sources, key=lambda x: x.get("height", 0)).get(
                    "url", ""
                )
        elif "images" in album_data:
            images = album_data["images"]
            if images:
                cover_url = max(images, key=lambda x: x.get("height", 0)).get("url", "")

        duration = 0
        if "trackDuration" in t:
            duration = t["trackDuration"].get("totalMilliseconds", 0)
        elif "duration_ms" in t:
            duration = t["duration_ms"]

        tracks.append(
            {
                "id": f"sp:{track_id}",
                "name": name,
                "artists": [a for a in artists if a] or ["Unknown Artist"],
                "album": album_name,
                "album_artists": album_artists,
                "track_number": t.get("trackNumber") or t.get("track_number", i),
                "disc_number": t.get("discNumber") or t.get("disc_number", 1),
                "tracks_count": 0,
                "duration": duration,
                "cover_url": cover_url,
                "source_url": f"https://open.spotify.com/track/{track_id}",
                "isrc": None,
                "release_date": None,
                "genres": [],
                "_album_uri": album_data.get("uri", ""),
            }
        )
    return tracks


def get_playlist_tracks(url):
    import re, spotapi

    match = re.search(r"playlist/([a-zA-Z0-9]+)", url)
    if not match:
        raise ValueError("Invalid Spotify playlist URL")
    playlist_id = match.group(1)
    p = spotapi.PublicPlaylist(playlist_id)
    raw_tracks = []
    try:
        for page in p.paginate_playlist():
            if isinstance(page, dict) and "items" in page:
                raw_tracks.extend(page["items"])
    except KeyError as e:
        if "content" in str(e):
            raise ValueError("Playlist not found or is private on Spotify.")
        raise
    return _parse_spotapi_track_list(raw_tracks)


def get_album_tracks(url):
    import re, spotapi

    match = re.search(r"album/([a-zA-Z0-9]+)", url)
    if not match:
        raise ValueError("Invalid Spotify album URL")
    album_id = match.group(1)
    a = spotapi.PublicAlbum(album_id)
    raw_tracks = []

    try:
        for page in a.paginate_album():
            if isinstance(page, list):
                raw_tracks.extend(page)
    except KeyError:
        raise ValueError("Album not found or unavailable in your region.")

    info = a.get_album_info()
    album_data = info.get("data", {}).get("albumUnion", {})

    real_album_name = album_data.get("name", "Unknown Album")
    real_album_artists = []
    if "artists" in album_data and "items" in album_data["artists"]:
        real_album_artists = [
            a.get("profile", {}).get("name", "") for a in album_data["artists"]["items"]
        ]
    real_album_artists = [a for a in real_album_artists if a] or ["Unknown Artist"]

    tracks = _parse_spotapi_track_list(raw_tracks)
    for t in tracks:
        if t.get("album") == "Unknown Album":
            t["album"] = real_album_name
        if not t.get("album_artists") or t.get("album_artists") == ["Unknown Artist"]:
            t["album_artists"] = real_album_artists
        if not t.get("_album_uri"):
            t["_album_uri"] = album_data.get("uri", "")

        if not t.get("cover_url"):
            cover_sources = album_data.get("coverArt", {}).get("sources", [])
            if cover_sources:
                t["cover_url"] = max(
                    cover_sources, key=lambda x: x.get("height", 0)
                ).get("url", "")
        if not t.get("release_date"):
            date_data = album_data.get("date", {})
            release_date = date_data.get("isoString") or date_data.get("year")
            if release_date:
                t["release_date"] = str(release_date).split("T")[0]
    return tracks


def get_single_track(url):
    import re, spotapi

    match = re.search(r"track/([a-zA-Z0-9]+)", url)
    if not match:
        raise ValueError("Invalid Spotify track URL")
    track_id = match.group(1)
    data = spotapi.Public.song_info(track_id)
    track_data = data.get("data", {}).get("trackUnion", {})
    if not track_data:
        raise ValueError("Could not extract track metadata via spotapi.")
    return _parse_spotapi_track_list([track_data])


def get_youtube_tracks(url):
    ydl_opts: dict[str, Any] = {
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
                            "album_artists": [
                                info.get("uploader") or "Various Artists"
                            ],
                            "track_number": i,
                            "disc_number": 1,
                            "tracks_count": 0,
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
                    "tracks_count": 1,
                    "cover_url": info.get("thumbnail") or "",
                    "source_url": url,
                    "direct_url": url,
                }
            )

        total_tracks = len(tracks)
        for t in tracks:
            t["tracks_count"] = total_tracks

        return tracks


def get_apple_music_tracks(url):
    html = _session.get(url, timeout=_DEFAULT_TIMEOUT).content.decode("utf-8")

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
                                    "tracks_count": 0,
                                    "cover_url": cover_url,
                                    "source_url": track_url,
                                    "direct_url": None,
                                }
                            )

    total_tracks = len(tracks)
    for t in tracks:
        t["tracks_count"] = total_tracks

    if "?i=" in url:
        adam_match = re.search(r"\?i=(\d+)", url)
        if adam_match:
            target_id = adam_match.group(1)
            filtered = [t for t in tracks if target_id in t["id"]]
            if filtered:
                return filtered

    return tracks


def fetch_tracks(url):
    import json, re

    if "spotify.com" in url:
        if "playlist/" in url:
            tracks = get_playlist_tracks(url)
            return _enrich_metadata(tracks)
        elif "album/" in url:
            tracks = get_album_tracks(url)
            return _enrich_metadata(tracks)
        elif "track/" in url:
            tracks = get_single_track(url)
            return _enrich_metadata(tracks)

    elif "music.apple.com" in url:
        tracks = get_apple_music_tracks(url)
        return _enrich_metadata(tracks)
    elif (
        "youtube.com" in url
        or "youtu.be" in url
        or "soundcloud.com" in url
        or "tidal.com" in url
    ):
        tracks = get_youtube_tracks(url)
        return _enrich_metadata(tracks)

    raise ValueError(
        "URL must be a supported Spotify, Apple Music, Tidal, YouTube, or SoundCloud link."
    )


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
