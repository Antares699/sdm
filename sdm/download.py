import os
import re
import glob
import yt_dlp
from math import exp
from pathlib import Path
import logging
import threading
import json
import subprocess
from threading import Lock
from difflib import SequenceMatcher
from sdm.metadata import get_lrclib_lyrics, _session

logging.getLogger("yt_dlp").setLevel(logging.ERROR)

_cover_cache = {}
_cache_lock = Lock()

_ffmpeg_path = None

_ytmusic = None
_ytmusic_lock = Lock()

_thread_local = threading.local()


def _get_ydl(opts, outtmpl, logger):
    if not hasattr(_thread_local, "ydl"):
        import copy

        base_opts = copy.deepcopy(opts)
        base_opts["outtmpl"] = outtmpl
        base_opts["logger"] = logger
        _thread_local.ydl = yt_dlp.YoutubeDL(base_opts)
    else:
        if isinstance(_thread_local.ydl.params.get("outtmpl"), dict):
            _thread_local.ydl.params["outtmpl"]["default"] = outtmpl
        else:
            _thread_local.ydl.params["outtmpl"] = outtmpl
        _thread_local.ydl.params["logger"] = logger
    return _thread_local.ydl


def _apply_twopass_loudnorm(filepath, format_flag):
    temp_filepath = filepath.with_suffix(f".temp.{format_flag}")
    codec = {"m4a": "aac", "mp3": "libmp3lame", "flac": "flac", "opus": "libopus"}.get(
        format_flag, "aac"
    )

    cmd1 = [
        _get_ffmpeg_path(),
        "-y",
        "-hide_banner",
        "-i",
        str(filepath),
        "-af",
        "loudnorm=I=-14:LRA=11:TP=-1.5:print_format=json",
        "-f",
        "null",
        "-",
    ]
    try:
        res = subprocess.run(cmd1, capture_output=True, text=True, check=True)
        match = re.search(r"\{.*?\}", res.stderr, re.DOTALL)
        if not match:
            raise ValueError("No JSON found in Pass 1 output")
        stats = json.loads(match.group(0))

        measured_i = stats.get("input_i")
        measured_lra = stats.get("input_lra")
        measured_tp = stats.get("input_tp")
        measured_thresh = stats.get("input_thresh")
        target_offset = stats.get("target_offset")

        if not all(
            [measured_i, measured_lra, measured_tp, measured_thresh, target_offset]
        ):
            raise ValueError("Missing values in JSON")

        cmd2 = [
            _get_ffmpeg_path(),
            "-y",
            "-hide_banner",
            "-i",
            str(filepath),
            "-af",
            f"loudnorm=I=-14:LRA=11:TP=-1.5:measured_I={measured_i}:measured_LRA={measured_lra}:measured_TP={measured_tp}:measured_thresh={measured_thresh}:offset={target_offset}:linear=true",
            "-c:a",
            codec,
        ]
        if format_flag == "mp3":
            cmd2.extend(["-q:a", "2"])
        elif format_flag == "m4a":
            cmd2.extend(["-b:a", "256k"])
        elif format_flag == "opus":
            cmd2.extend(["-b:a", "128k"])

        cmd2.extend(["-vn", str(temp_filepath)])
        subprocess.run(cmd2, capture_output=True, check=True)

        if temp_filepath.exists():
            temp_filepath.replace(filepath)
    except Exception:
        if temp_filepath.exists():
            try:
                temp_filepath.unlink()
            except Exception:
                pass

        cmd_fallback = [
            _get_ffmpeg_path(),
            "-y",
            "-hide_banner",
            "-i",
            str(filepath),
            "-af",
            "loudnorm=I=-14:LRA=11:TP=-1.5",
            "-c:a",
            codec,
        ]
        if format_flag == "mp3":
            cmd_fallback.extend(["-q:a", "2"])
        elif format_flag == "m4a":
            cmd_fallback.extend(["-b:a", "256k"])
        elif format_flag == "opus":
            cmd_fallback.extend(["-b:a", "128k"])
        cmd_fallback.extend(["-vn", str(temp_filepath)])

        try:
            subprocess.run(cmd_fallback, capture_output=True, check=True)
            if temp_filepath.exists():
                temp_filepath.replace(filepath)
        except Exception:
            if temp_filepath.exists():
                try:
                    temp_filepath.unlink()
                except Exception:
                    pass


def _get_ytmusic():
    global _ytmusic
    if _ytmusic is None:
        with _ytmusic_lock:
            if _ytmusic is None:
                from ytmusicapi import YTMusic

                _ytmusic = YTMusic()
    return _ytmusic


def _get_ffmpeg_path():
    global _ffmpeg_path
    if _ffmpeg_path is None:
        import shutil

        system_ffmpeg = shutil.which("ffmpeg")
        if system_ffmpeg:
            _ffmpeg_path = system_ffmpeg
        else:
            import imageio_ffmpeg

            _ffmpeg_path = imageio_ffmpeg.get_ffmpeg_exe()
    return _ffmpeg_path


def _get_cached_cover(cover_url):
    if not cover_url:
        return None
    with _cache_lock:
        if cover_url in _cover_cache:
            return _cover_cache[cover_url]
    try:
        data = _session.get(cover_url, timeout=10).content
    except Exception:
        data = None
    with _cache_lock:
        _cover_cache[cover_url] = data
    return data


class YTDLLogger:
    def __init__(self):
        self.last_error = ""

    def debug(self, msg):
        pass

    def info(self, msg):
        pass

    def warning(self, msg):
        pass

    def error(self, msg):
        self.last_error = msg


def sanitize_filename(name):
    safe_name = str(name or "Unknown")
    safe_name = (
        safe_name.replace("’", "'")
        .replace("‘", "'")
        .replace("“", '"')
        .replace("”", '"')
        .replace("–", "-")
    )
    safe_name = safe_name.encode("ascii", "ignore").decode("ascii")
    return re.sub(r'[\\/*?:"<>|]', "", safe_name).strip()


def sanitize_ytdlp_error(error_msg):
    if not error_msg:
        return "Unknown error"

    error_msg = str(error_msg)

    error_msg = error_msg.replace("ERROR: ", "")
    error_msg = re.sub(r"\[youtube\]\s*[a-zA-Z0-9_-]+:\s*", "", error_msg)

    error_msg = re.sub(
        r"Requested format is not available.*",
        "YouTube DRM blocked audio stream (Upstream yt-dlp issue)",
        error_msg,
    )
    error_msg = re.sub(
        r"Use --list-formats.*",
        "YouTube DRM blocked audio stream (Upstream yt-dlp issue)",
        error_msg,
    )
    error_msg = re.sub(
        r"Use --cookies-from-browser.*", "Requires cookies to bypass.", error_msg
    )

    if "WinError 32" in error_msg:
        return "File temporarily locked by OS during rename (WinError 32)"
    error_msg = re.sub(
        r"Sign in to confirm you.*?re not a bot.*",
        "YouTube DRM blocked audio stream (Upstream yt-dlp issue)",
        error_msg,
    )
    error_msg = re.sub(
        r"Confirm you are on the latest version using\s+yt-dlp -U.*", "", error_msg
    )

    error_msg = re.sub(
        r"Please report this issue on\s+https://github\.com/yt-dlp/yt-dlp/issues.*",
        "Please report this issue on the sdm GitHub repository.",
        error_msg,
    )

    if "[WinError 32]" in error_msg:
        return "File temporarily locked by OS during rename (WinError 32)"

    error_msg = re.sub(r"\s+", " ", error_msg).strip()
    return error_msg


def _cleanup_partial_files(base_filepath):
    for partial in Path(base_filepath).parent.glob(Path(base_filepath).name + ".*"):
        try:
            partial.unlink()
        except Exception:
            pass


_FORBIDDEN_WORDS = [
    "remix",
    "live",
    "cover",
    "karaoke",
    "instrumental",
    "acoustic",
    "slowed",
    "reverb",
    "sped up",
    "nightcore",
    "8d audio",
    "8d",
    "bass boosted",
    "bassboosted",
    "concert",
    "acapella",
    "a capella",
    "reaction",
    "tutorial",
    "lesson",
    "mashup",
]


def _slugify(text):
    text = text.lower()

    text = re.sub(
        r"\s*-\s*\d{4}\s*(remaster(ed)?|master|mix|edition|version).*",
        "",
        text,
        flags=re.IGNORECASE,
    )

    text = re.sub(
        r"\s*-\s*(remastered|deluxe|anniversary|expanded|bonus track|edit|version"
        r"|single version|album version|mono|stereo|radio edit|explicit|master).*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r"\s*\(.*?\)\s*", " ", text)
    text = re.sub(r"\s*\[.*?\]\s*", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _score_ytmusic_result(result, target_track):
    target_title = target_track.get("name", "Unknown")
    target_artists = target_track.get("artists", ["Unknown Artist"])
    target_artist = target_artists[0] if target_artists else "Unknown"
    target_duration_ms = target_track.get("duration", 0)
    target_album = target_track.get("album", "")
    target_explicit = target_track.get("explicit", False)

    result_title = result.get("title", "")
    result_artists = [a.get("name", "") for a in result.get("artists", [])]
    result_artist_str = ", ".join(result_artists).lower()
    result_duration_s = result.get("duration_seconds") or 0
    result_album = (
        result.get("album", {}).get("name", "")
        if isinstance(result.get("album"), dict)
        else ""
    )
    result_explicit = result.get("isExplicit", False)

    slug_target = _slugify(target_title)
    slug_result = _slugify(result_title)

    result_lower = result_title.lower()
    target_lower = target_title.lower()
    for word in _FORBIDDEN_WORDS:
        if word in result_lower and word not in target_lower:
            return -1

    duration_score = 0
    if result_duration_s and target_duration_ms:
        target_duration_s = target_duration_ms / 1000
        diff_s = abs(result_duration_s - target_duration_s)

        if diff_s > 30:
            return -1
        if diff_s <= 5:
            duration_score = 35 - (diff_s * 1)
        else:
            duration_score = 30 * exp(-0.15 * (diff_s - 5))

    title_ratio = SequenceMatcher(None, slug_target, slug_result).ratio()

    if slug_target and slug_result:
        if slug_target in slug_result or slug_result in slug_target:
            containment_ratio = min(len(slug_target), len(slug_result)) / max(
                len(slug_target), len(slug_result)
            )
            title_ratio = max(title_ratio, containment_ratio, 0.75)

    if title_ratio < 0.5:
        return -1

    title_score = title_ratio * 35

    artist_score = 0
    best_artist_ratio = 0

    for ta in target_artists:
        slug_ta = _slugify(ta)
        for ra in result_artists:
            ratio = SequenceMatcher(None, slug_ta, _slugify(ra)).ratio()
            best_artist_ratio = max(best_artist_ratio, ratio)

    slug_primary_target = _slugify(target_artist)
    if slug_primary_target in result_artist_str or any(
        _slugify(ra) in slug_primary_target for ra in result_artists
    ):
        best_artist_ratio = max(best_artist_ratio, 0.85)

    if best_artist_ratio < 0.4 and slug_primary_target:
        slug_result_title_lower = slug_result if slug_result else _slugify(result_title)
        artist_words = slug_primary_target.split()
        significant_words = [w for w in artist_words if len(w) > 2]
        if significant_words:
            matches = sum(1 for w in significant_words if w in slug_result_title_lower)
            if matches >= len(significant_words) * 0.5:
                best_artist_ratio = max(best_artist_ratio, 0.7)

    if best_artist_ratio < 0.4:
        return -1

    artist_score = best_artist_ratio * 30

    total_score = int(duration_score + title_score + artist_score)

    if target_album and result_album:
        if _slugify(target_album) == _slugify(result_album):
            total_score += 15
        elif _slugify(target_album) in _slugify(result_album) or _slugify(
            result_album
        ) in _slugify(target_album):
            total_score += 5

    if target_explicit != result_explicit:
        total_score -= 20

    return total_score


def _search_ytmusic(track):
    title = track.get("name", "Unknown")
    artists = track.get("artists", [])
    artist = artists[0] if artists else "Unknown"

    all_artists = (
        ", ".join(str(a) for a in artists) if len(artists) > 1 else str(artist)
    )
    search_query = f"{all_artists} - {title}"

    ytm = _get_ytmusic()
    best_url = None
    best_score = -1

    try:
        song_results = ytm.search(search_query, filter="songs", limit=10)
    except Exception:
        song_results = []

    for r in song_results:
        score = _score_ytmusic_result(r, track)
        if score > best_score:
            best_score = score
            video_id = r.get("videoId")
            if video_id:
                best_url = f"https://music.youtube.com/watch?v={video_id}"

        if best_score >= 95:
            return best_url

    try:
        video_results = ytm.search(search_query, filter="videos", limit=10)
    except Exception:
        video_results = []

    for r in video_results:
        score = _score_ytmusic_result(r, track)
        if score > best_score:
            best_score = score
            video_id = r.get("videoId")
            if video_id:
                best_url = f"https://www.youtube.com/watch?v={video_id}"

    if best_score < 70:
        fallback_query = f"{title} {artist}"
        try:
            fallback_results = ytm.search(fallback_query, filter="songs", limit=5)
            for r in fallback_results:
                score = _score_ytmusic_result(r, track)
                if score > best_score:
                    best_score = score
                    video_id = r.get("videoId")
                    if video_id:
                        best_url = f"https://music.youtube.com/watch?v={video_id}"
        except Exception:
            pass

    if best_score < 30:
        return None

    return best_url


def embed_metadata(filepath, track, fetch_lyrics=False, resize_covers=False):
    import time

    for attempt in range(5):
        try:
            success = _embed_metadata_inner(
                filepath, track, fetch_lyrics, resize_covers
            )
            if success:
                return True
        except PermissionError:
            if attempt < 4:
                time.sleep(1.5)
                continue
            return False
        except Exception:
            return False
    return False


def _embed_metadata_inner(filepath, track, fetch_lyrics=False, resize_covers=False):
    ext = filepath.suffix.lower()
    name = str(track.get("name", "Unknown"))
    artists = track.get("artists", [])
    primary_artist = str(artists[0]) if artists else "Unknown"
    album_name = str(track.get("album", "Unknown"))
    album_artists = track.get("album_artists", [])
    album_artist_str = (
        ", ".join(str(a) for a in album_artists) if album_artists else primary_artist
    )
    track_num = int(track.get("track_number") or 1)
    track_total = int(track.get("tracks_count") or 0)
    disc_num = int(track.get("disc_number") or 1)

    release_date = track.get("release_date")
    genres = track.get("genres", [])
    genre = ", ".join(g.title() for g in genres) if genres else None

    lyrics = ""
    if fetch_lyrics:
        lyrics = get_lrclib_lyrics(
            name, primary_artist, album_name if album_name != "Unknown Album" else None
        )

    cover_data = _get_cached_cover(track.get("cover_url"))

    if cover_data and resize_covers:
        try:
            from PIL import Image
            import io

            img = Image.open(io.BytesIO(cover_data))
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.thumbnail((600, 600), Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="JPEG", quality=85)
            cover_data = out.getvalue()
        except Exception:
            pass

    try:
        success = False
        if ext == ".m4a":
            from mutagen.mp4 import MP4, MP4Cover

            audio = MP4(filepath)
            audio["\xa9nam"] = [name]
            audio["\xa9ART"] = [primary_artist]
            audio["\xa9alb"] = [album_name]
            if album_artists:
                audio["aART"] = [album_artist_str]
            audio["trkn"] = [(track_num, track_total)]
            audio["disk"] = [(disc_num, 0)]
            if track.get("source_url"):
                audio["----:spotdl:WOAS"] = [str(track["source_url"]).encode("utf-8")]
            if genre:
                audio["\xa9gen"] = [str(genre)]
            if release_date:
                audio["\xa9day"] = [str(release_date)]
            if lyrics:
                audio["\xa9lyr"] = [str(lyrics)]
            if cover_data:
                audio["covr"] = [MP4Cover(cover_data, imageformat=MP4Cover.FORMAT_JPEG)]

            if track.get("wiki"):
                audio["\xa9cmt"] = [str(track.get("wiki"))]
            if track.get("mbid"):
                audio["----:com.apple.iTunes:MusicBrainz Track Id"] = [
                    str(track.get("mbid")).encode("utf-8")
                ]

            audio.save()
            success = True

        elif ext == ".mp3":
            from mutagen.mp3 import MP3
            from mutagen.id3 import (
                ID3,
                TIT2,
                TPE1,
                TALB,
                TPE2,
                TRCK,
                TPOS,
                TCON,
                TDRC,
                USLT,
                APIC,
            )

            try:
                audio = MP3(filepath, ID3=ID3)
            except Exception:
                audio = MP3(filepath)
            if audio.tags is None:
                audio.add_tags()
            audio.tags.add(TIT2(encoding=3, text=name))
            audio.tags.add(TPE1(encoding=3, text=primary_artist))
            audio.tags.add(TALB(encoding=3, text=album_name))
            audio.tags.add(TPE2(encoding=3, text=album_artist_str))
            if track_total > 0:
                audio.tags.add(TRCK(encoding=3, text=f"{track_num}/{track_total}"))
            else:
                audio.tags.add(TRCK(encoding=3, text=str(track_num)))
            audio.tags.add(TPOS(encoding=3, text=str(disc_num)))
            if genre:
                audio.tags.add(TCON(encoding=3, text=genre))
            if release_date:
                audio.tags.add(TDRC(encoding=3, text=release_date[:4]))
            if lyrics:
                audio.tags.add(USLT(encoding=3, lang="eng", desc="", text=lyrics))
            if cover_data:
                audio.tags.add(
                    APIC(
                        encoding=3,
                        mime="image/jpeg",
                        type=3,
                        desc="Cover",
                        data=cover_data,
                    )
                )

            from mutagen.id3 import COMM, TXXX

            if track.get("wiki"):
                audio.tags.add(
                    COMM(encoding=3, lang="eng", desc="", text=[str(track.get("wiki"))])
                )
            if track.get("mbid"):
                audio.tags.add(
                    TXXX(
                        encoding=3,
                        desc="MusicBrainz Track Id",
                        text=[str(track.get("mbid"))],
                    )
                )

            audio.save()
            success = True

        elif ext in [".flac", ".opus"]:
            from mutagen.flac import Picture
            import base64

            if ext == ".flac":
                from mutagen.flac import FLAC

                audio = FLAC(filepath)
            else:
                from mutagen.oggopus import OggOpus

                audio = OggOpus(filepath)

            audio["TITLE"] = name
            audio["ARTIST"] = primary_artist
            audio["ALBUM"] = album_name
            audio["ALBUMARTIST"] = album_artist_str
            audio["TRACKNUMBER"] = str(track_num)
            if track_total > 0:
                audio["TRACKTOTAL"] = str(track_total)
            audio["DISCNUMBER"] = str(disc_num)
            if genre:
                audio["GENRE"] = genre
            if release_date:
                audio["DATE"] = release_date[:4]
            if lyrics:
                audio["LYRICS"] = lyrics

            if cover_data:
                pic = Picture()
                pic.type = 3
                pic.mime = "image/jpeg"
                pic.desc = "Cover"
                pic.data = cover_data

                if ext == ".flac":
                    audio.add_picture(pic)
                else:
                    audio["metadata_block_picture"] = [
                        base64.b64encode(pic.write()).decode("ascii")
                    ]

            if track.get("wiki"):
                audio["DESCRIPTION"] = str(track.get("wiki"))
            if track.get("mbid"):
                audio["MUSICBRAINZ_TRACKID"] = str(track.get("mbid"))

            audio.save()
            success = True

        else:
            return False

        if success:
            return True

    except PermissionError:
        raise
    except Exception:
        return False


def get_zen_profile_path():
    try:
        profiles_dir = os.path.expandvars(r"%APPDATA%\Zen\Profiles")
        cookies_files = glob.glob(
            os.path.join(profiles_dir, "**", "cookies.sqlite"), recursive=True
        )
        if cookies_files:
            latest = max(cookies_files, key=os.path.getmtime)
            return os.path.dirname(latest)
    except Exception:
        pass
    return None


def build_ydl_opts(
    cookies_source=None,
    format_flag="m4a",
    sponsor_block=False,
    normalize=False,
):
    postprocessors = [{"key": "FFmpegExtractAudio", "preferredcodec": format_flag}]

    ydl_opts = {
        "ffmpeg_location": _get_ffmpeg_path(),
        "format": f"{format_flag}/bestaudio/best",
        "postprocessors": postprocessors,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "geo_bypass": True,
    }

    if sponsor_block:
        ydl_opts["sponsorblock_remove"] = [
            "music_offtopic",
            "intro",
            "outro",
            "sponsor",
        ]

    if cookies_source:
        if Path(cookies_source).is_file():
            ydl_opts["cookiefile"] = cookies_source
        elif cookies_source.lower() == "zen":
            zen_path = get_zen_profile_path()
            if zen_path:
                ydl_opts["cookiesfrombrowser"] = ("firefox", zen_path, None, None)
        else:
            ydl_opts["cookiesfrombrowser"] = (cookies_source, None, None, None)

    return ydl_opts


def download_and_tag(
    track,
    output_dir,
    track_index,
    cookies_source=None,
    format_flag="m4a",
    fallback=False,
    sponsor_block=False,
    normalize=False,
    fetch_lyrics=False,
    ydl_opts=None,
    refresh_metadata=False,
    resize_covers=False,
):
    title = sanitize_filename(track.get("name"))
    artists = track.get("artists", [])
    artist = sanitize_filename(artists[0] if artists else "Unknown")

    filename_template = f"{track_index:02d} - {artist} - {title}"
    base_filepath = Path(output_dir) / filename_template
    final_path_obj = Path(str(base_filepath) + f".{format_flag}")

    if final_path_obj.exists():
        if refresh_metadata:
            if not embed_metadata(final_path_obj, track, fetch_lyrics, resize_covers):
                return "error", "Failed to write metadata (file might be locked)"
        return "success", final_path_obj.name

    direct_url = track.get("direct_url")
    yt_logger = YTDLLogger()

    if ydl_opts is not None:
        opts = ydl_opts
    else:
        opts = build_ydl_opts(cookies_source, format_flag, sponsor_block, False)

    ydl = _get_ydl(opts, str(base_filepath) + ".%(ext)s", yt_logger)

    _download_succeeded = False
    try:
        if direct_url:
            video_url = direct_url
        else:
            video_url = _search_ytmusic(track)
            if not video_url:
                return "error", "No confident match found on YouTube Music"

        yt_logger.last_error = ""

        try:
            import time

            for attempt in range(5):
                try:
                    download_info = ydl.extract_info(video_url, download=True)
                    break
                except Exception as dl_e:
                    if "[WinError 32]" in str(dl_e):
                        if attempt < 4:
                            time.sleep(1.5)
                            continue
                    raise dl_e

            final_filepath = None
            if (
                "requested_downloads" in download_info
                and download_info["requested_downloads"]
            ):
                final_filepath = download_info["requested_downloads"][0].get("filepath")

            if not final_filepath:
                final_filepath = str(base_filepath) + f".{format_flag}"

            final_path_obj = Path(final_filepath)

            if final_path_obj.exists():
                if normalize:
                    _apply_twopass_loudnorm(final_path_obj, format_flag)
                if not embed_metadata(
                    final_path_obj, track, fetch_lyrics, resize_covers
                ):
                    return "error", "Failed to write metadata (file might be locked)"
                _download_succeeded = True
                return "success", final_path_obj.name

            return "error", "Downloaded file not found"

        except yt_dlp.utils.DownloadError as e:
            error_msg = str(e)
            if (
                "Sign in to confirm your age" in error_msg
                or "Sign in to confirm your age" in yt_logger.last_error
            ):
                if fallback:
                    result = _soundcloud_fallback(
                        track,
                        base_filepath,
                        format_flag,
                        ydl,
                        yt_logger,
                        fetch_lyrics,
                        normalize,
                        resize_covers,
                    )
                    if result[0] in ("fallback_success", "success"):
                        _download_succeeded = True
                    return result
                return (
                    "drm_blocked",
                    "YouTube DRM blocked audio stream (Upstream yt-dlp issue)",
                )
            elif (
                "DPAPI" in error_msg
                or "decrypt" in error_msg
                or "DPAPI" in yt_logger.last_error
                or "decrypt" in yt_logger.last_error
            ):
                return "encryption_error", "Browser cookies are encrypted"
            elif (
                "locked" in error_msg.lower()
                or "locked" in yt_logger.last_error.lower()
            ):
                return "error", "Cookie database is locked. Close your browser."
            else:
                clean = yt_logger.last_error if yt_logger.last_error else error_msg
                clean = sanitize_ytdlp_error(clean)

                if "DRM" in clean or "Age restricted" in clean:
                    if fallback:
                        result = _soundcloud_fallback(
                            track,
                            base_filepath,
                            format_flag,
                            ydl,
                            yt_logger,
                            fetch_lyrics,
                            normalize,
                            resize_covers,
                        )
                        if result[0] in ("fallback_success", "success"):
                            _download_succeeded = True
                        return result
                    return "drm_blocked", clean

                return "error", clean

        except Exception as e:
            return "error", sanitize_ytdlp_error(str(e))
    finally:
        if not _download_succeeded:
            _cleanup_partial_files(base_filepath)


def _soundcloud_fallback(
    track,
    base_filepath,
    format_flag,
    ydl,
    yt_logger,
    fetch_lyrics,
    normalize=False,
    resize_covers=False,
):
    title = track.get("name", "Unknown")
    artists = track.get("artists", [])
    artist = artists[0] if artists else "Unknown"
    search_query = f"{artist} {title}"

    yt_logger.last_error = ""
    try:
        sc_info = ydl.extract_info(f"scsearch1:{search_query}", download=False)
        if sc_info and "entries" in sc_info and sc_info["entries"]:
            sc_entry = sc_info["entries"][0]
            sc_url = sc_entry.get("url")
            if sc_url:
                download_info = ydl.extract_info(sc_url, download=True)
                final_filepath = None
                if (
                    "requested_downloads" in download_info
                    and download_info["requested_downloads"]
                ):
                    final_filepath = download_info["requested_downloads"][0].get(
                        "filepath"
                    )
                if not final_filepath:
                    final_filepath = str(base_filepath) + f".{format_flag}"

                final_path_obj = Path(final_filepath)
                if final_path_obj.exists():
                    if normalize:
                        _apply_twopass_loudnorm(final_path_obj, format_flag)
                    if not embed_metadata(
                        final_path_obj, track, fetch_lyrics, resize_covers
                    ):
                        return (
                            "error",
                            "Failed to write metadata (file might be locked)",
                        )
                    return "fallback_success", final_path_obj.name
    except Exception as e:
        return "error", sanitize_ytdlp_error(str(e))

    return (
        "drm_blocked",
        "YouTube DRM blocked audio stream (Upstream yt-dlp issue)",
    )


def embed_lastfm_metadata(filepath, genres, wiki, mbid):
    import logging
    from mutagen.mp4 import MP4
    from mutagen.id3 import ID3, TCON, COMM, TXXX

    logger = logging.getLogger(__name__)

    try:
        genre_str = ", ".join(genres) if genres else None
        ext = filepath.suffix.lower()

        if ext == ".m4a":
            audio = MP4(filepath)
            if genre_str:
                audio["©gen"] = [genre_str]
            if wiki:
                audio["©cmt"] = [wiki]
            if mbid:
                audio["----:com.apple.iTunes:MusicBrainz Track Id"] = [
                    mbid.encode("utf-8")
                ]
            audio.save()
            return True

        elif ext == ".mp3":
            try:
                audio = ID3(filepath)
            except Exception:
                audio = ID3()
            if genre_str:
                audio.add(TCON(encoding=3, text=[genre_str]))
            if wiki:
                audio.add(COMM(encoding=3, lang="eng", desc="", text=[wiki]))
            if mbid:
                audio.add(TXXX(encoding=3, desc="MusicBrainz Track Id", text=[mbid]))
            audio.save(filepath)
            return True

        elif ext in [".flac", ".opus"]:
            import mutagen

            audio = mutagen.File(filepath)
            if not audio:
                return False
            if genre_str:
                audio["genre"] = [genre_str]
            if wiki:
                audio["description"] = wiki
            if mbid:
                audio["musicbrainz_trackid"] = mbid
            audio.save()
            return True

    except Exception as e:
        logger.error(f"Error embedding Last.fm metadata in {filepath}: {e}")
        return False
    return False
