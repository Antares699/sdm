from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import typer
from enum import Enum

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
    url: str,
    output_dir: Path,
    format: AudioFormat,
    cleanup: bool,
    dry_run: bool,
    no_delete: bool,
    workers: int,
    cookies: str,
    sponsor_block: bool,
    normalize: bool,
    lyrics: bool,
    refresh_metadata: bool,
):
    sync_manager = SyncManager(output_dir)
    if url:
        sync_manager.set_source_url(url)
    else:
        url = sync_manager.get_source_url()
        if not url:
            console.print(
                "[bold red]Error:[/] No source URL found in this directory. Please run `sdm download <url>` once to link it."
            )
            raise typer.Exit(code=1)

    if dry_run:
        no_delete = True

    console.print("[bold green]sdm: Gathering metadata...[/bold green]")
    try:
        tracks = fetch_tracks(url)
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

        import threading
        import yt_dlp

        shared_ydl_opts = build_ydl_opts(
            cookies, format.value, sponsor_block, normalize
        )
        worker_ydl_instances = {}
        _ydl_lock = threading.Lock()

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
                # Get or create a per-worker ydl instance
                tid = threading.current_thread().ident
                with _ydl_lock:
                    if tid not in worker_ydl_instances:
                        worker_ydl_instances[tid] = yt_dlp.YoutubeDL(
                            dict(shared_ydl_opts)
                        )
                ydl_instance = worker_ydl_instances[tid]

                return download_and_tag(
                    track,
                    output_dir,
                    index,
                    format_flag=format.value,
                    fallback=False,
                    fetch_lyrics=lyrics,
                    ydl=ydl_instance,
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

        # Flush any remaining batched sync data to disk
        sync_manager.flush()

        # Clean up per-worker yt-dlp instances
        for ydl_inst in worker_ydl_instances.values():
            try:
                ydl_inst.close()
            except Exception:
                pass
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

        # Flush any sync data from DRM fallback downloads
        sync_manager.flush()

    if not dry_run:
        m3u_path = output_dir / "_playlist.m3u"
        try:
            with open(m3u_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for t in tracks:
                    t_id = t.get("id")
                    fname = sync_manager.get_filename(t_id)
                    if fname:
                        f.write(f"{fname}\n")
        except Exception as e:
            console.print(f"[yellow]Warning:[/] Failed to generate _playlist.m3u ({e})")

    console.print("\n[bold green]Done![/bold green]")


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

        # Use the shared cached ffmpeg path (with system PATH detection)
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

        if normalize:
            cmd.extend(["-af", "loudnorm=I=-14:LRA=11:TP=-1.5"])

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


def main():
    app()


if __name__ == "__main__":
    main()
