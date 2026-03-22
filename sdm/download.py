import os
import re
import glob
import yt_dlp
from pathlib import Path
import logging
from threading import Lock
from sdm.metadata import get_itunes_metadata, get_lrclib_lyrics, _session

logging.getLogger("yt_dlp").setLevel(logging.ERROR)

_cover_cache = {}
_itunes_cache = {}
_cache_lock = Lock()

_ffmpeg_path = None

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
    """Download cover art with caching. Returns bytes or None."""
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


def _get_cached_itunes(track_name, artist_name):
    """Fetch iTunes metadata with caching. Returns (genre, release_date)."""
    key = (artist_name.lower().strip(), track_name.lower().strip())
    with _cache_lock:
        if key in _itunes_cache:
            return _itunes_cache[key]
    genre, release_date = get_itunes_metadata(track_name, artist_name)
    with _cache_lock:
        _itunes_cache[key] = (genre, release_date)
    return genre, release_date


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

    error_msg = re.sub(r"\s+", " ", error_msg).strip()
    return error_msg


def _score_match(entry, target_title, target_artist, target_duration_ms):
    """Score a YouTube search result against target track metadata."""
    from difflib import SequenceMatcher

    score = 0

    entry_duration = entry.get("duration")  # seconds
    if entry_duration and target_duration_ms:
        diff_ms = abs(entry_duration * 1000 - target_duration_ms)
        if diff_ms < 3000:
            score += 40  
        elif diff_ms < 10000:
            score += 25  
        elif diff_ms < 30000:
            score += 10  

    entry_title = (entry.get("title") or "").lower()
    title_ratio = SequenceMatcher(None, target_title.lower(), entry_title).ratio()
    score += int(title_ratio * 30)

    channel = (entry.get("uploader") or entry.get("channel") or "").lower()
    if target_artist.lower() in channel:
        score += 20
    elif SequenceMatcher(None, target_artist.lower(), channel).ratio() > 0.6:
        score += 10

    if "official" in entry_title or "audio" in entry_title:
        score += 10

    if any(w in entry_title for w in ["remix", "live", "cover", "karaoke"]):
        if not any(w in target_title.lower() for w in ["remix", "live", "cover"]):
            score -= 15

    return score


def embed_metadata(filepath, track, fetch_lyrics=False):
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
    track_total = int(track.get("tracks_count") or track_num)
    disc_num = int(track.get("disc_number") or 1)

    genre, release_date = _get_cached_itunes(name, primary_artist)
    lyrics = ""
    if fetch_lyrics:
        lyrics = get_lrclib_lyrics(
            name, primary_artist, album_name if album_name != "Unknown Album" else None
        )

    cover_data = _get_cached_cover(track.get("cover_url"))

    try:
        if ext == ".m4a":
            from mutagen.mp4 import MP4, MP4Cover

            audio = MP4(filepath)
            audio["\xa9nam"] = [name]
            audio["\xa9ART"] = [primary_artist]
            if len(artists) > 1:
                audio["aART"] = [", ".join(str(a) for a in artists)]
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
            audio.save()
            return True

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
            audio.tags.add(TRCK(encoding=3, text=f"{track_num}/{track_total}"))
            audio.tags.add(TPOS(encoding=3, text=f"{disc_num}/1"))
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
            audio.save()
            return True

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
            audio.save()
            return True

        else:
            return False

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
    """Build YoutubeDL options shared across all tracks in a batch."""
    postprocessors = [{"key": "FFmpegExtractAudio", "preferredcodec": format_flag}]

    ydl_opts = {
        "ffmpeg_location": _get_ffmpeg_path(),
        "format": f"{format_flag}/bestaudio/best",
        "postprocessors": postprocessors,
        "quiet": True,
        "no_warnings": True,
        "nocheckcertificate": True,
        "geo_bypass": True,
        "extract_flat": "in_playlist",
    }

    if normalize:
        ydl_opts["postprocessor_args"] = {
            "ffmpeg": ["-af", "loudnorm=I=-14:LRA=11:TP=-1.5"]
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
    ydl=None,
):
    title = sanitize_filename(track.get("name"))
    artists = track.get("artists", [])
    artist = sanitize_filename(artists[0] if artists else "Unknown")

    filename_template = f"{track_index:02d} - {artist} - {title}"
    base_filepath = Path(output_dir) / filename_template

    direct_url = track.get("direct_url")
    search_query = f"{artist} - {title} audio"
    yt_logger = YTDLLogger()

    own_ydl = False
    if ydl is not None:
        ydl.params["outtmpl"]["default"] = str(base_filepath) + ".%(ext)s"
        ydl.params["logger"] = yt_logger
    else:
        own_ydl = True
        ydl_opts = build_ydl_opts(cookies_source, format_flag, sponsor_block, normalize)
        ydl_opts["outtmpl"] = str(base_filepath) + ".%(ext)s"
        ydl_opts["logger"] = yt_logger
        ydl = yt_dlp.YoutubeDL(ydl_opts)

    last_error_message = "No search results found"

    try:
        try:
            if direct_url:
                info = {"entries": [{"url": direct_url}]}
            else:
                info = ydl.extract_info(f"ytsearch3:{search_query}", download=False)
        except Exception as e:
            return "error", sanitize_ytdlp_error(str(e))

        if not info or "entries" not in info or not info["entries"]:
            return "error", "No search results found"

        entries = [e for e in info["entries"] if e]
        target_duration = track.get("duration")
        if not direct_url and target_duration:
            scored = [
                (e, _score_match(e, title, artist, target_duration)) for e in entries
            ]
            scored.sort(key=lambda x: x[1], reverse=True)
            entries = [e for e, s in scored]

        for entry in entries:
            if not entry:
                continue

            video_url = entry.get("url") or entry.get("webpage_url")
            if not video_url:
                continue

            yt_logger.last_error = ""

            try:
                # Attempt to download the specific video
                download_info = ydl.extract_info(video_url, download=True)

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
                    embed_metadata(final_path_obj, track, fetch_lyrics)
                    return "success", final_path_obj.name

            except yt_dlp.utils.DownloadError as e:
                error_msg = str(e)
                if (
                    "Sign in to confirm your age" in error_msg
                    or "Sign in to confirm your age" in yt_logger.last_error
                ):
                    last_error_message = "Age restricted content"
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
                    clean_error = (
                        yt_logger.last_error if yt_logger.last_error else error_msg
                    )
                    last_error_message = sanitize_ytdlp_error(clean_error)
                continue

            except Exception as e:
                last_error_message = sanitize_ytdlp_error(str(e))
                continue

        # If we exhausted all 3 entries and none worked
        is_drm_blocked = (
            "YouTube DRM blocked audio stream" in last_error_message
            or "Age restricted" in last_error_message
        )

        if fallback and is_drm_blocked:
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
                            final_filepath = download_info["requested_downloads"][
                                0
                            ].get("filepath")
                        if not final_filepath:
                            final_filepath = str(base_filepath) + f".{format_flag}"

                        final_path_obj = Path(final_filepath)
                        if final_path_obj.exists():
                            embed_metadata(final_path_obj, track, fetch_lyrics)
                            return "fallback_success", final_path_obj.name
            except Exception as e:
                last_error_message = sanitize_ytdlp_error(str(e))
        elif is_drm_blocked:
            return (
                "drm_blocked",
                "YouTube DRM blocked audio stream (Upstream yt-dlp issue)",
            )

        if last_error_message == "Age restricted content":
            return "age_restricted", "All 3 search results were age restricted"
        elif last_error_message == "Browser cookies are encrypted":
            return "encryption_error", last_error_message

        return "error", f"All 3 search results failed ({last_error_message})"

    except Exception as e:
        return "error", sanitize_ytdlp_error(str(e))
    finally:
        if own_ydl:
            ydl.close()
