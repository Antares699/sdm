from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import typer
from enum import Enum
import typing

from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TimeRemainingColumn,
)
from rich.table import Table
from rich.prompt import Confirm

from sdm.metadata import fetch_tracks
from sdm.download import (
    download_and_tag,
    sanitize_filename,
    embed_metadata,
    _get_ffmpeg_path,
    build_ydl_opts,
)
from sdm.sync import SyncManager

app = typer.Typer(
    name="sdm",
    help="SDM - A fast, lightweight, and reliable CLI tool to download and sync Spotify, Apple Music, Tidal, and YouTube playlists.",
    add_completion=False,
    rich_markup_mode="rich",
)

console = Console()


class AudioFormat(str, Enum):
    m4a = "m4a"
    mp3 = "mp3"
    flac = "flac"
    opus = "opus"


def execute_sync_or_download(
    url: typing.Optional[str],
    output_dir: Path,
    format: AudioFormat,
    cleanup: bool,
    dry_run: bool,
    no_delete: bool,
    workers: int,
    cookies: typing.Optional[str],
    sponsor_block: bool,
    normalize: bool,
    lyrics: bool,
    refresh_metadata: bool,
):
    is_static = False
    if url:
        url_str = str(url).lower()
        if (
            "/album/" in url_str
            or "/track/" in url_str
            or "watch?v=" in url_str
            or "youtu.be" in url_str
        ):
            is_static = True
        elif "soundcloud.com" in url_str and "/sets/" not in url_str:
            is_static = True

    sync_manager = SyncManager(output_dir, is_static=is_static)
    source_url = url
    if source_url:
        sync_manager.set_source_url(source_url)
    else:
        source_url = sync_manager.get_source_url()
        if not source_url:
            console.print(
                "[bold red]Error:[/] No source URL found in this directory. Please run `sdm download <url>` once to link it."
            )
            raise typer.Exit(code=1)

    if dry_run:
        no_delete = True
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[bold green]sdm: Gathering metadata...[/bold green]"),
            transient=True,
            console=console,
        ) as progress:
            progress.add_task("metadata", total=None)
            tracks = fetch_tracks(source_url)
    except Exception as e:
        console.print(f"[bold red]Error fetching metadata:[/] {e}")
        raise typer.Exit(code=1)

    if not tracks:
        console.print("[bold red]No tracks found. Aborting.[/]")
        raise typer.Exit(code=1)

    console.print(f"[bold cyan]Found {len(tracks)} tracks.[/bold cyan]")

    current_ids = set()
    tracks_to_download = []
    index_mapping = {}
    skipped_count = 0
    refreshed_count = 0
    downloaded_count = 0
    error_count = 0
    age_restricted_count = 0
    encryption_error_count = 0
    drm_blocked_tracks = []
    deleted_files = []

    for index, track in enumerate(tracks, start=1):
        track_id = track.get("id")
        if not track_id:
            continue

        index_mapping[track_id] = index
        current_ids.add(track_id)

        is_synced = sync_manager.is_synced(track_id)
        if is_synced and not refresh_metadata:
            skipped_count += 1
            continue

        tracks_to_download.append((index, track, is_synced))

    if not dry_run:
        sync_manager.update_index_map(index_mapping)

    if skipped_count > 0:
        console.print(
            f"[yellow]Skipping {skipped_count} tracks that are already synced.[/yellow]"
        )

    if tracks_to_download:
        action_verb = (
            "Simulating"
            if dry_run
            else ("Refreshing" if refresh_metadata else "Downloading")
        )
        console.print(
            f"{action_verb} {len(tracks_to_download)} tracks using {workers} workers..."
        )

        shared_ydl_opts = build_ydl_opts(
            cookies, format.value, sponsor_block, normalize
        )

        def worker(index, track, is_synced):
            title = track.get("name", "Unknown")
            if dry_run:
                if not is_synced:
                    return "dry_run_success", "Simulated download"
                else:
                    return "dry_run_refresh", "Simulated refresh"

            if is_synced:
                filename = sync_manager.get_filename(track["id"])
                if not filename:
                    return "error", "Filename not found in sync index"
                filepath = output_dir / filename
                if not filepath.exists():
                    return "error", "File not found on disk"
                if embed_metadata(filepath, track, fetch_lyrics=lyrics):
                    return "success", filename
                else:
                    return "error", "Failed to refresh metadata"
            else:
                return download_and_tag(
                    track,
                    output_dir,
                    index,
                    cookies_source=cookies,
                    format_flag=format.value,
                    fallback=False,
                    sponsor_block=sponsor_block,
                    normalize=normalize,
                    fetch_lyrics=lyrics,
                    ydl_opts=shared_ydl_opts,
                    refresh_metadata=refresh_metadata,
                )

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            "[progress.percentage]{task.percentage:>3.0f}%",
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            main_task = progress.add_task(
                f"[bold cyan]{action_verb}...", total=len(tracks_to_download)
            )

            with ThreadPoolExecutor(max_workers=workers) as executor:
                future_to_track = {
                    executor.submit(worker, index, track, is_synced): (
                        track,
                        index,
                        is_synced,
                    )
                    for index, track, is_synced in tracks_to_download
                }

                for future in as_completed(future_to_track):
                    track, index, is_synced = future_to_track[future]
                    title = track.get("name", "Unknown")

                    try:
                        status, message = future.result()

                        if status == "success":
                            if not is_synced:
                                sync_manager.mark_synced(track["id"], message)
                                downloaded_count += 1
                                progress.console.print(f"[green][+][/green] {title}")
                            else:
                                refreshed_count += 1
                                progress.console.print(
                                    f"[blue][*][/blue] Refreshed: {title}"
                                )
                        elif status == "dry_run_success":
                            downloaded_count += 1
                            progress.console.print(
                                f"[green][~][/green] Would download: {title}"
                            )
                        elif status == "dry_run_refresh":
                            refreshed_count += 1
                            progress.console.print(
                                f"[blue][~][/blue] Would refresh: {title}"
                            )

                        elif status == "drm_blocked":
                            drm_blocked_tracks.append((track, index))
                            progress.console.print(
                                f"[yellow][!][/yellow] DRM Blocked: {title} (Will prompt for fallback later)"
                            )
                        elif status == "fallback_success":
                            sync_manager.mark_synced(track["id"], message)
                            downloaded_count += 1
                            progress.console.print(
                                f"[yellow][!][/yellow] Fallback (SoundCloud): {title} (Original blocked by DRM)"
                            )
                        elif status == "age_restricted":
                            age_restricted_count += 1
                            progress.console.print(
                                f"[red][!][/red] Age Restricted: {title} (Use --cookies firefox or cookies.txt)"
                            )
                        elif status == "encryption_error":
                            encryption_error_count += 1
                            progress.console.print(
                                f"[red][X][/red] Failed: {title} (Chrome/Edge cookies are encrypted. Use Firefox or export a cookies.txt file)"
                            )
                        else:
                            error_count += 1
                            progress.console.print(
                                f"[red][-][/red] Failed: {title} ({message})"
                            )
                    except Exception as e:
                        error_count += 1
                        progress.console.print(
                            f"[red][-][/red] Exception processing {title}: {e}"
                        )

                    progress.advance(main_task)

        sync_manager.flush()
    else:
        console.print("[green]All tracks are already up to date.[/green]")

    if cleanup and not no_delete:
        console.print("[yellow]Performing sync cleanup...[/yellow]")
        deleted_files = sync_manager.cleanup(
            current_ids, dry_run=dry_run, no_delete=no_delete
        )
        for f in deleted_files:
            prefix = "[~] Would delete:" if dry_run else "[-] Deleted:"
            console.print(f"[red]{prefix}[/] {f}")

    table = Table(title=f"sdm Summary {'(DRY RUN)' if dry_run else ''}")
    table.add_column("Metric", style="cyan")
    table.add_column("Count", style="magenta")

    table.add_row("Downloaded", str(downloaded_count))
    if refresh_metadata:
        table.add_row("Refreshed", str(refreshed_count))
    table.add_row("Skipped (Synced)", str(skipped_count))
    if drm_blocked_tracks:
        table.add_row("DRM Blocked", str(len(drm_blocked_tracks)), style="yellow")
    if age_restricted_count > 0:
        table.add_row("Age Restricted", str(age_restricted_count), style="red")
    if encryption_error_count > 0:
        table.add_row("Encryption Errors", str(encryption_error_count), style="red")
    if error_count > 0:
        table.add_row("Other Errors", str(error_count), style="red")
    if cleanup:
        table.add_row("Deleted (Sync)", str(len(deleted_files)))

    console.print("\n")
    console.print(table)

    if drm_blocked_tracks and not dry_run:
        console.print(
            f"\n[bold yellow][!] {len(drm_blocked_tracks)} tracks were blocked by YouTube's DRM.[/bold yellow]"
        )
        for track, idx in drm_blocked_tracks:
            console.print(f"  {idx}. {track.get('name', 'Unknown')}")

        console.print("\n[cyan]You have two options to resolve this:[/cyan]")
        console.print(
            "  [bold]A)[/] Try downloading them automatically via SoundCloud."
        )
        console.print(
            "     [yellow](CAUTION: SoundCloud official tracks are heavily copyright-striked. These may be pitched-down or unofficial bootlegs).[/yellow]"
        )
        console.print(
            "  [bold]B)[/] Download the pristine audio yourself and use the 'sdm inject' command."
        )

        do_fallback = Confirm.ask(
            "\nWould you like to try the SoundCloud fallback for these tracks now?"
        )

        if do_fallback:
            console.print("\n[cyan]Attempting SoundCloud fallback...[/cyan]")
            with Progress(
                SpinnerColumn(),
                TextColumn("[progress.description]{task.description}"),
                BarColumn(),
                "[progress.percentage]{task.percentage:>3.0f}%",
                TimeRemainingColumn(),
                console=console,
            ) as fb_progress:
                fb_task = fb_progress.add_task(
                    "[bold yellow]Fallback...", total=len(drm_blocked_tracks)
                )
                for track, index in drm_blocked_tracks:
                    title = track.get("name", "Unknown")
                    try:
                        f_status, f_message = download_and_tag(
                            track,
                            output_dir,
                            index,
                            cookies_source=cookies,
                            format_flag=format.value,
                            fallback=True,
                            sponsor_block=sponsor_block,
                            normalize=normalize,
                            fetch_lyrics=lyrics,
                            refresh_metadata=refresh_metadata,
                        )
                        if f_status in ["fallback_success", "success"]:
                            sync_manager.mark_synced(track["id"], f_message)
                            fb_progress.console.print(
                                f"[yellow][!][/yellow] Fallback Success: {title}"
                            )
                        else:
                            fb_progress.console.print(
                                f"[red][-][/red] Fallback Failed: {title} ({f_message})"
                            )
                    except Exception as e:
                        fb_progress.console.print(
                            f"[red][-][/red] Exception: {title} ({e})"
                        )
                    fb_progress.advance(fb_task)
        else:
            console.print(
                "\n[green]No problem! When you download the files yourself, run:[/green]"
            )
            console.print(
                f'  sdm inject "your_file.mp3" "<track_url>" -o "{output_dir}"'
            )

        sync_manager.flush()

    if not dry_run and not is_static:
        m3u_path = output_dir / "_playlist.m3u"
        try:
            with open(m3u_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for t in tracks:
                    t_id = t.get("id")
                    fname = sync_manager.get_filename(t_id)
                    if fname:
                        f.write(fname + "\n")
        except Exception as e:
            console.print(f"[yellow]Warning:[/] Failed to generate _playlist.m3u ({e})")

    console.print("\n[bold green]Done![/bold green]")


@app.command(help="Search and download a single track directly from YouTube Music.")
def search(
    query: str = typer.Argument(..., help="Track name to search for"),
    output: Path = typer.Option(
        ".", "--output", "-o", help="Output directory", rich_help_panel="Output Options"
    ),
    format: AudioFormat = typer.Option(
        AudioFormat.m4a,
        "--format",
        "-f",
        help="Audio codec to use",
        rich_help_panel="Output Options",
    ),
    lyrics: bool = typer.Option(
        False,
        "--lyrics",
        help="Automatically fetch and embed synced lyrics",
        rich_help_panel="Metadata & Tags",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate the download [red]without making changes[/red]",
    ),
):
    from ytmusicapi import YTMusic

    console.print(f"[cyan]Searching for '[bold]{query}[/bold]'...[/cyan]")
    ytmusic = YTMusic()
    try:
        results = ytmusic.search(query, filter="songs", limit=10)
    except Exception as e:
        console.print(f"[bold red]Search failed:[/] {e}")
        raise typer.Exit(1)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        raise typer.Exit()

    results = results[:10]

    table = Table(title="Search Results", show_header=True, header_style="bold magenta")
    table.add_column("#", style="dim", justify="right")
    table.add_column("Title")
    table.add_column("Artist")
    table.add_column("Album")
    table.add_column("Duration", justify="right")

    for i, r in enumerate(results, start=1):
        title = r.get("title", "Unknown")

        artists_data = r.get("artists", [])
        artists = (
            ", ".join([a["name"] for a in artists_data if "name" in a])
            if artists_data
            else "Unknown"
        )

        album_data = r.get("album")
        album = album_data.get("name", "Unknown") if album_data else "Unknown"

        duration = r.get("duration", "0:00")
        table.add_row(str(i), title, artists, album, duration)

    console.print(table)

    from rich.prompt import IntPrompt

    choice = IntPrompt.ask(
        "Enter the number of the track to download",
        choices=[str(i) for i in range(len(results) + 1)],
        show_choices=False,
        default=0,
    )

    if choice == 0:
        console.print("Cancelled.")
        raise typer.Exit()

    selected = results[choice - 1]
    vid = selected.get("videoId")
    if not vid:
        console.print("[bold red]Selected track has no video ID.[/bold red]")
        raise typer.Exit(1)

    url = f"https://music.youtube.com/watch?v={vid}"

    output_dir = output.resolve()
    execute_sync_or_download(
        url=url,
        output_dir=output_dir,
        format=format,
        cleanup=False,
        dry_run=dry_run,
        no_delete=False,
        workers=1,
        cookies=None,
        sponsor_block=False,
        normalize=False,
        lyrics=lyrics,
        refresh_metadata=False,
    )


@app.command(
    help="Download a Playlist, Album, or Track from supported streaming services."
)
def download(
    url: str = typer.Argument(..., help="Playlist, Album, or Track URL"),
    output: Path = typer.Option(
        ".", "--output", "-o", help="Output directory", rich_help_panel="Output Options"
    ),
    format: AudioFormat = typer.Option(
        AudioFormat.m4a,
        "--format",
        "-f",
        help="Audio codec to use",
        rich_help_panel="Output Options",
    ),
    cleanup: bool = typer.Option(
        False,
        "--cleanup",
        help="Delete local files that are no longer in the remote list (Same as sync)",
        rich_help_panel="Sync Options",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate the download/sync process [red]without making changes[/red]",
        rich_help_panel="Sync Options",
    ),
    no_delete: bool = typer.Option(
        False,
        "--no-delete",
        help="Do not delete any local files during cleanup",
        rich_help_panel="Sync Options",
    ),
    workers: int = typer.Option(
        3,
        "--workers",
        "-w",
        help="Number of concurrent downloads",
        rich_help_panel="Performance",
    ),
    cookies: str = typer.Option(
        None,
        "--cookies",
        "-c",
        help="Browser name (e.g., firefox) or path to cookies.txt",
        rich_help_panel="Authentication",
    ),
    sponsor_block: bool = typer.Option(
        False,
        "--sponsor-block",
        help="Use SponsorBlock to trim non-music sections",
        rich_help_panel="Audio Processing",
    ),
    normalize: bool = typer.Option(
        False,
        "--normalize",
        help="Apply EBU R128 (-14 LUFS) audio normalization",
        rich_help_panel="Audio Processing",
    ),
    lyrics: bool = typer.Option(
        False,
        "--lyrics",
        help="Automatically fetch and embed synced lyrics",
        rich_help_panel="Metadata & Tags",
    ),
    refresh_metadata: bool = typer.Option(
        False,
        "--refresh-metadata",
        "--refresh",
        help="Force re-tagging of existing local files",
        rich_help_panel="Metadata & Tags",
    ),
):
    output_dir = output.resolve()
    kwargs = _merge_config(locals())
    format = kwargs.get("format", format)
    workers = kwargs.get("workers", workers)
    cookies = kwargs.get("cookies", cookies)
    sponsor_block = kwargs.get("sponsor_block", sponsor_block)
    normalize = kwargs.get("normalize", normalize)
    lyrics = kwargs.get("lyrics", lyrics)

    execute_sync_or_download(
        url=url,
        output_dir=output_dir,
        format=format,
        cleanup=cleanup,
        dry_run=dry_run,
        no_delete=no_delete,
        workers=workers,
        cookies=cookies,
        sponsor_block=sponsor_block,
        normalize=normalize,
        lyrics=lyrics,
        refresh_metadata=refresh_metadata,
    )


@app.command(help="Sync a previously downloaded directory with its source URL.")
def sync(
    dir: Path = typer.Argument(".", help="Directory to sync"),
    format: AudioFormat = typer.Option(
        AudioFormat.m4a,
        "--format",
        "-f",
        help="Audio codec to use for new tracks",
        rich_help_panel="Output Options",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Simulate the sync process [red]without making changes[/red]",
        rich_help_panel="Sync Options",
    ),
    no_delete: bool = typer.Option(
        False,
        "--no-delete",
        help="Do not delete any local files during sync",
        rich_help_panel="Sync Options",
    ),
    workers: int = typer.Option(
        3,
        "--workers",
        "-w",
        help="Number of concurrent downloads",
        rich_help_panel="Performance",
    ),
    cookies: str = typer.Option(
        None,
        "--cookies",
        "-c",
        help="Browser name (e.g., firefox) or path to cookies.txt",
        rich_help_panel="Authentication",
    ),
    sponsor_block: bool = typer.Option(
        False,
        "--sponsor-block",
        help="Use SponsorBlock to trim non-music sections",
        rich_help_panel="Audio Processing",
    ),
    normalize: bool = typer.Option(
        False,
        "--normalize",
        help="Apply EBU R128 (-14 LUFS) audio normalization",
        rich_help_panel="Audio Processing",
    ),
    lyrics: bool = typer.Option(
        False,
        "--lyrics",
        help="Automatically fetch and embed synced lyrics",
        rich_help_panel="Metadata & Tags",
    ),
    refresh_metadata: bool = typer.Option(
        False,
        "--refresh-metadata",
        "--refresh",
        help="Force re-tagging of existing local files",
        rich_help_panel="Metadata & Tags",
    ),
):
    output_dir = dir.resolve()
    kwargs = _merge_config(locals())
    format = kwargs.get("format", format)
    workers = kwargs.get("workers", workers)
    cookies = kwargs.get("cookies", cookies)
    sponsor_block = kwargs.get("sponsor_block", sponsor_block)
    normalize = kwargs.get("normalize", normalize)
    lyrics = kwargs.get("lyrics", lyrics)

    execute_sync_or_download(
        url=None,
        output_dir=output_dir,
        format=format,
        cleanup=True,
        dry_run=dry_run,
        no_delete=no_delete,
        workers=workers,
        cookies=cookies,
        sponsor_block=sponsor_block,
        normalize=normalize,
        lyrics=lyrics,
        refresh_metadata=refresh_metadata,
    )


@app.command(
    help="Inject a local audio file, apply metadata, and protect it from sync deletion."
)
def inject(
    file: Path = typer.Argument(
        ...,
        exists=True,
        file_okay=True,
        dir_okay=False,
        readable=True,
        help="Path to the local audio file to inject",
    ),
    url: str = typer.Argument(..., help="Track URL to extract metadata and tags from"),
    output: Path = typer.Option(
        ".", "--output", "-o", help="Output directory", rich_help_panel="Output Options"
    ),
    format: AudioFormat = typer.Option(
        AudioFormat.m4a,
        "--format",
        "-f",
        help="Output format for injected file",
        rich_help_panel="Output Options",
    ),
    index: int = typer.Option(
        0,
        "--index",
        help="Track index to use for naming",
        rich_help_panel="Metadata & Tags",
    ),
    normalize: bool = typer.Option(
        False,
        "--normalize",
        help="Apply EBU R128 (-14 LUFS) audio normalization",
        rich_help_panel="Audio Processing",
    ),
    lyrics: bool = typer.Option(
        False,
        "--lyrics",
        help="Automatically fetch and embed synced lyrics",
        rich_help_panel="Metadata & Tags",
    ),
):
    output_dir = output.resolve()
    sync_manager = SyncManager(output_dir)

    console.print(
        "[bold green]sdm: Fetching metadata for injected track...[/bold green]"
    )
    try:
        tracks = fetch_tracks(url)
        if not tracks:
            console.print("[bold red]Error:[/] No track metadata found for the URL.")
            raise typer.Exit(code=1)

        if len(tracks) > 1:
            console.print(
                "[bold red]Error:[/] You provided a Playlist or Album URL. Please provide a specific TRACK URL when injecting a single file."
            )
            raise typer.Exit(code=1)

        track = tracks[0]
        if index:
            index_val = index
        else:
            cached_index = sync_manager.get_index(track.get("id"))
            index_val = cached_index if cached_index else track.get("track_number", 0)

        title = sanitize_filename(track.get("name"))
        artists = track.get("artists", [])
        artist = sanitize_filename(artists[0] if artists else "Unknown")
        filename_template = f"{index_val:02d} - {artist} - {title}.{format.value}"
        final_filepath = output_dir / filename_template

        console.print(f"[cyan]Converting and importing {filename_template}...[/cyan]")

        import subprocess

        cmd = [_get_ffmpeg_path(), "-y", "-i", str(file)]

        codec = {"m4a": "aac", "mp3": "libmp3lame", "flac": "flac", "opus": "libopus"}[
            format.value
        ]
        cmd.extend(["-c:a", codec])

        if format.value == "mp3":
            cmd.extend(["-q:a", "2"])
        elif format.value == "m4a":
            cmd.extend(["-b:a", "256k"])
        elif format.value == "opus":
            cmd.extend(["-b:a", "128k"])

        cmd.extend(["-vn", str(final_filepath)])

        try:
            subprocess.run(
                cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
        except FileNotFoundError:
            console.print(
                "[bold red]Error:[/] ffmpeg binary not found. Please report this issue."
            )
            raise typer.Exit(code=1)
        except subprocess.CalledProcessError:
            console.print("[bold red]Error:[/] ffmpeg failed to process the file.")
            raise typer.Exit(code=1)

        if normalize:
            console.print("[cyan]Applying 2-pass normalization...[/cyan]")
            from sdm.download import _apply_twopass_loudnorm

            _apply_twopass_loudnorm(final_filepath, format.value)

        console.print("[cyan]Embedding metadata...[/cyan]")
        if embed_metadata(final_filepath, track, fetch_lyrics=lyrics):
            sync_manager.mark_injected(track["id"], final_filepath.name)
            console.print(
                f"[bold green]Successfully injected and synced:[/] {filename_template}"
            )
        else:
            console.print("[bold red]Error:[/] Failed to embed metadata.")
            raise typer.Exit(code=1)

    except Exception as e:
        console.print(f"[bold red]Error during injection:[/] {e}")
        raise typer.Exit(code=1)


import json
from rich.table import Table

CONFIG_FILE = Path.home() / ".sdm_config.json"


def load_config():
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_config(data):
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4)


def _merge_config(kwargs):
    cfg = load_config()
    if kwargs.get("format") == AudioFormat.m4a and "format" in cfg:
        try:
            kwargs["format"] = AudioFormat(cfg["format"])
        except:
            pass
    if kwargs.get("workers") == 3 and "workers" in cfg:
        kwargs["workers"] = cfg["workers"]
    if kwargs.get("cookies") is None and "cookies" in cfg:
        kwargs["cookies"] = cfg["cookies"]
    if kwargs.get("sponsor_block") is False and "sponsor_block" in cfg:
        kwargs["sponsor_block"] = cfg["sponsor_block"]
    if kwargs.get("normalize") is False and "normalize" in cfg:
        kwargs["normalize"] = cfg["normalize"]
    if kwargs.get("lyrics") is False and "lyrics" in cfg:
        kwargs["lyrics"] = cfg["lyrics"]
    return kwargs


@app.command(help="Save default settings for download and sync.")
def config(
    format: str = typer.Option(
        None, "--format", "-f", help="Default audio format (m4a, mp3, flac, opus)"
    ),
    workers: int = typer.Option(
        None, "--workers", "-w", help="Default number of workers"
    ),
    cookies: str = typer.Option(
        None, "--cookies", "-c", help="Browser for cookies (e.g., firefox)"
    ),
    sponsor_block: bool = typer.Option(
        None, "--sponsor-block", help="Use SponsorBlock"
    ),
    normalize: bool = typer.Option(
        None, "--normalize", help="Apply audio normalization"
    ),
    lyrics: bool = typer.Option(None, "--lyrics", help="Fetch lyrics"),
    lastfm_key: str = typer.Option(
        None, "--lastfm-key", help="Last.fm API Key for extensive metadata"
    ),
    clear: bool = typer.Option(False, "--clear", help="Clear all saved configurations"),
):
    if clear:
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
        console.print("[bold green]Configuration cleared.[/bold green]")
        raise typer.Exit()

    cfg = load_config()
    updated = False

    if format is not None:
        cfg["format"] = format
        updated = True
    if workers is not None:
        cfg["workers"] = workers
        updated = True
    if cookies is not None:
        cfg["cookies"] = cookies
        updated = True
    if sponsor_block is not None:
        cfg["sponsor_block"] = sponsor_block
        updated = True
    if normalize is not None:
        cfg["normalize"] = normalize
        updated = True
    if lyrics is not None:
        cfg["lyrics"] = lyrics
        updated = True
    if lastfm_key is not None:
        cfg["lastfm_key"] = lastfm_key
        updated = True

    if updated:
        save_config(cfg)
        console.print(f"[bold green]Configuration saved to {CONFIG_FILE}[/bold green]")

    table = Table(
        title="Current Configuration", show_header=True, header_style="bold magenta"
    )
    table.add_column("Key")
    table.add_column("Value")
    for k, v in load_config().items():
        table.add_row(str(k), str(v))
    console.print(table)


@app.command(
    help="Recursively scan a directory and enrich existing files with Last.fm metadata."
)
def tag(
    dir: Path = typer.Argument(..., help="Directory to tag"),
):
    cfg = load_config()
    lastfm_key = cfg.get("lastfm_key")
    if not lastfm_key:
        console.print(
            "[bold red]Error:[/] Last.fm API key not configured. Please run `sdm config --lastfm-key YOUR_KEY` first."
        )
        raise typer.Exit(1)

    output_dir = dir.resolve()
    audio_files = []
    for ext in ["*.m4a", "*.mp3", "*.flac", "*.opus"]:
        audio_files.extend(output_dir.rglob(ext))

    if not audio_files:
        console.print(f"[yellow]No audio files found in {output_dir}.[/yellow]")
        raise typer.Exit()

    console.print(
        f"[bold cyan]Found {len(audio_files)} audio files. Fetching Last.fm metadata...[/bold cyan]"
    )

    from sdm.metadata import get_lastfm_metadata
    from sdm.download import embed_lastfm_metadata
    import mutagen
    import re

    def get_tags(filepath):
        try:
            audio = mutagen.File(filepath)
            if not audio:
                return None, None
            ext = filepath.suffix.lower()
            if ext == ".m4a":
                return audio.get("\xa9ART", [""])[0], audio.get("\xa9nam", [""])[0]
            elif ext == ".mp3":
                return str(audio.get("TPE1", "")), str(audio.get("TIT2", ""))
            elif ext in [".flac", ".opus"]:
                return audio.get("artist", [""])[0], audio.get("title", [""])[0]
        except Exception:
            return None, None
        return None, None

    def process_file(filepath):
        artist, title = get_tags(filepath)
        if not artist or not title:
            return "skipped", filepath.name

        clean_title = re.sub(
            r"[\(\[].*?(feat|ft|remaster|radio|edit|mix).*?[\)\]]",
            "",
            title,
            flags=re.I,
        ).strip()

        genres, wiki, mbid = get_lastfm_metadata(artist, clean_title, lastfm_key)
        if genres or wiki or mbid:
            if embed_lastfm_metadata(filepath, genres, wiki, mbid):
                return "success", f"{artist} - {title}"
        return "not_found", f"{artist} - {title}"

    success_count = 0
    skipped_count = 0
    not_found_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task(
            "[bold cyan]Tagging library...", total=len(audio_files)
        )

        with ThreadPoolExecutor(max_workers=5) as executor:
            future_to_file = {executor.submit(process_file, f): f for f in audio_files}
            for future in as_completed(future_to_file):
                status, name = future.result()
                if status == "success":
                    success_count += 1
                    progress.console.print(f"[green][+][/green] Enriched: {name}")
                elif status == "skipped":
                    skipped_count += 1
                    progress.console.print(
                        f"[yellow][~][/yellow] Skipped (No Artist/Title): {name}"
                    )
                else:
                    not_found_count += 1
                    progress.console.print(
                        f"[yellow][-][/yellow] No Last.fm data: {name}"
                    )
                progress.advance(task)

    console.print(
        f"\n[bold green]Finished tagging {success_count} files![/bold green] (Not Found: {not_found_count}, Skipped: {skipped_count})"
    )


@app.command(help="Show statistics for the local library.")
def stats(
    dir: Path = typer.Argument(".", help="Directory to check"),
):
    output_dir = dir.resolve()
    sync_manager = SyncManager(output_dir)

    rows = sync_manager.conn.execute("SELECT track_id, filename FROM tracks").fetchall()
    injected_rows = sync_manager.conn.execute(
        "SELECT track_id FROM injected"
    ).fetchall()
    injected_ids = {r[0] for r in injected_rows}

    source_url = sync_manager.get_source_url()

    total_tracks = len(rows)
    total_injected = len(injected_ids)

    total_size = 0
    formats = {}
    missing = 0

    for _, filename in rows:
        filepath = output_dir / filename
        if filepath.exists():
            total_size += filepath.stat().st_size
            ext = filepath.suffix.lower()
            formats[ext] = formats.get(ext, 0) + 1
        else:
            missing += 1

    size_mb = total_size / (1024 * 1024)

    table = Table(
        title=f"Library Statistics: {output_dir.name}",
        show_header=True,
        header_style="bold magenta",
    )
    table.add_column("Metric", style="cyan")
    table.add_column("Value", style="green")

    table.add_row("Source URL", source_url or "None")
    table.add_row("Total Tracks", str(total_tracks))
    table.add_row("Injected Files", str(total_injected))
    table.add_row("Total Size", f"{size_mb:.2f} MB")

    format_str = ", ".join(f"{ext}: {count}" for ext, count in formats.items())
    table.add_row("Formats", format_str or "None")

    if missing > 0:
        table.add_row("Missing Files", str(missing), style="red")

    console.print(table)


@app.command(help="Bulk convert a downloaded library to a different audio format.")
def migrate(
    dir: Path = typer.Option(..., "--dir", help="Target directory"),
    target_format: AudioFormat = typer.Argument(
        ..., help="Target audio format (m4a, mp3, flac, opus)"
    ),
):
    output_dir = dir.resolve()
    sync_manager = SyncManager(output_dir)

    rows = sync_manager.conn.execute("SELECT track_id, filename FROM tracks").fetchall()
    if not rows:
        console.print("[yellow]No tracked files found in directory.[/yellow]")
        raise typer.Exit()

    console.print(
        f"[bold cyan]Migrating {len(rows)} files to {target_format.value}...[/bold cyan]"
    )
    import subprocess

    migrated_count = 0
    error_count = 0

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        TimeRemainingColumn(),
        console=console,
    ) as progress:
        task = progress.add_task("[bold cyan]Migrating...", total=len(rows))

        for track_id, filename in rows:
            filepath = output_dir / filename
            if (
                not filepath.exists()
                or filepath.suffix.lower() == f".{target_format.value}"
            ):
                progress.advance(task)
                continue

            new_filename = filepath.with_suffix(f".{target_format.value}").name
            new_filepath = output_dir / new_filename

            cmd = [_get_ffmpeg_path(), "-y", "-i", str(filepath)]
            codec = {
                "m4a": "aac",
                "mp3": "libmp3lame",
                "flac": "flac",
                "opus": "libopus",
            }[target_format.value]
            cmd.extend(["-c:a", codec])

            if target_format.value == "mp3":
                cmd.extend(["-q:a", "2"])
            elif target_format.value == "m4a":
                cmd.extend(["-b:a", "256k"])
            elif target_format.value == "opus":
                cmd.extend(["-b:a", "128k"])

            cmd.extend(["-vn", str(new_filepath)])

            try:
                subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

                sync_manager.conn.execute(
                    "UPDATE tracks SET filename = ? WHERE track_id = ?",
                    (new_filename, track_id),
                )
                sync_manager.conn.commit()

                filepath.unlink()
                migrated_count += 1
                progress.console.print(f"[green][+][/green] Migrated: {new_filename}")
            except Exception as e:
                error_count += 1
                progress.console.print(
                    f"[red][-][/red] Failed to migrate {filename}: {e}"
                )

            progress.advance(task)

    console.print(
        f"\n[bold green]Migration complete! Migrated {migrated_count} files.[/bold green] (Errors: {error_count})"
    )


def main():
    app()


if __name__ == "__main__":
    main()
