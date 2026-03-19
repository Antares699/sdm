import sys
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import subprocess
import imageio_ffmpeg
import typer

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
from sdm.download import download_and_tag, sanitize_filename, embed_metadata
from sdm.sync import SyncManager

app = typer.Typer(
    name="sdm",
    help="SDM - A fast, lightweight, and reliable CLI tool to download and sync Spotify playlists, albums, and tracks.",
    add_completion=False,
    rich_markup_mode="rich",
)

console = Console()


@app.command(
    help="Download and sync a Playlist, Album, or Track from Spotify, YouTube, or SoundCloud."
)
def download(
    url: str = typer.Argument(..., help="Playlist, Album, or Track URL"),
    output: Path = typer.Option(
        ".", "--output", "-o", help="Output directory (default: current directory)"
    ),
    sync: bool = typer.Option(
        False,
        "--sync",
        help="Delete local files that are no longer in the source list",
    ),
    workers: int = typer.Option(
        3, "--workers", "-w", help="Number of concurrent downloads (default: 3)"
    ),
    cookies: str = typer.Option(
        None,
        "--cookies",
        "-c",
        help="Browser name (e.g., firefox) or path to a cookies.txt file to bypass age restrictions",
    ),
    sponsor_block: bool = typer.Option(
        False,
        "--sponsor-block",
        help="Use SponsorBlock to automatically trim non-music sections from YouTube videos",
    ),
    normalize: bool = typer.Option(
        False,
        "--normalize",
        help="Apply EBU R128 (-14 LUFS) audio normalization to all tracks",
    ),
    lyrics: bool = typer.Option(
        False,
        "--lyrics",
        help="Automatically fetch and embed lyrics from LRCLIB",
    ),
    refresh_metadata: bool = typer.Option(
        False,
        "--refresh-metadata",
        "--refresh",
        help="Force re-tagging of existing local files with the latest metadata and lyrics",
    ),
):
    output_dir = output.resolve()
    sync_manager = SyncManager(output_dir)

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

    # Filter out already synced tracks
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

    sync_manager.update_index_map(index_mapping)

    if skipped_count > 0:
        console.print(
            f"[yellow]Skipping {skipped_count} tracks that are already synced.[/yellow]"
        )

    if tracks_to_download:
        action_verb = "Refreshing" if refresh_metadata else "Downloading"
        console.print(
            f"{action_verb} {len(tracks_to_download)} tracks using {workers} workers..."
        )

        def worker(index, track, is_synced):
            title = track.get("name", "Unknown")
            if is_synced:
                filename = sync_manager.data["tracks"].get(track["id"])
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
                    cookies,
                    "m4a",
                    False,
                    sponsor_block,
                    normalize,
                    lyrics,
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
                            f"[red][-][/red] Exception downloading {title}: {e}"
                        )

                    progress.advance(main_task)
    else:
        console.print("[green]All tracks are already up to date.[/green]")

    if sync:
        console.print("[yellow]Performing sync cleanup...[/yellow]")
        deleted_files = sync_manager.cleanup(current_ids)
        for f in deleted_files:
            console.print(f"[red][-] Deleted:[/] {f}")

    # Print summary table
    table = Table(title="sdm Summary")
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
    if sync:
        table.add_row("Deleted (Sync)", str(len(deleted_files)))

    console.print("\n")
    console.print(table)

    if drm_blocked_tracks:
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
            "  [bold]B)[/] Download the pristine MP3s yourself and use the 'sdm inject' command to weave them seamlessly into the playlist."
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
                            cookies,
                            "m4a",
                            True,
                            sponsor_block,
                            normalize,
                            lyrics,
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

    # Generate M3U file
    m3u_path = output_dir / "_playlist.m3u"
    try:
        with open(m3u_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for t in tracks:
                t_id = t.get("id")
                fname = sync_manager.data["tracks"].get(t_id)
                if fname:
                    f.write(f"{fname}\n")
    except Exception as e:
        console.print(f"[yellow]Warning:[/] Failed to generate _playlist.m3u ({e})")

    console.print("\n[bold green]Done![/bold green]")


@app.command(
    help="Inject a local audio file, apply Spotify metadata, and protect it from sync deletion."
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
    url: str = typer.Argument(
        ..., help="Spotify Track URL to extract metadata and tags from"
    ),
    output: Path = typer.Option(
        ".", "--output", "-o", help="Output directory (default: current directory)"
    ),
    index: int = typer.Option(
        0, "--index", help="Track index to use for naming when injecting (e.g., 103)"
    ),
    normalize: bool = typer.Option(
        False,
        "--normalize",
        help="Apply EBU R128 (-14 LUFS) audio normalization to the injected file",
    ),
    lyrics: bool = typer.Option(
        False,
        "--lyrics",
        help="Automatically fetch and embed lyrics from LRCLIB",
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
                "[bold red]Error:[/] You provided a Playlist or Album URL. Please provide a specific Spotify TRACK URL when injecting a single file."
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
        filename_template = f"{index_val:02d} - {artist} - {title}.m4a"
        final_filepath = output_dir / filename_template

        console.print(f"[cyan]Converting and importing {filename_template}...[/cyan]")

        cmd = [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-y",
            "-i",
            str(file),
            "-c:a",
            "aac",
            "-b:a",
            "256k",
        ]

        if normalize:
            cmd.extend(["-af", "loudnorm=I=-14:LRA=11:TP=-1.5"])

        cmd.extend(["-vn", str(final_filepath)])

        try:
            subprocess.run(
                cmd,
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
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
