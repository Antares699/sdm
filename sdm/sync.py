import json
import sqlite3
from pathlib import Path

class SyncManager:
    def __init__(self, output_dir, is_static=False):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.is_static = is_static

        if is_static:
            self.db_path = ":memory:"
        else:
            self.db_path = self.output_dir / ".sync.db"

        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()
        if not is_static:
            self._migrate_from_json()

    def _create_tables(self):
        self.conn.executescript("""
            CREATE TABLE IF NOT EXISTS tracks (
                track_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS injected (
                track_id TEXT PRIMARY KEY
            );
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT
            );
        """)
        self.conn.commit()

    def _migrate_from_json(self):
        json_path = self.output_dir / ".sync.json"
        if not json_path.exists():
            return
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                raw = json.load(f)

            if raw.get("_version") == 2:
                tracks = raw.get("tracks", {})
                injected = raw.get("injected", [])
                source_url = raw.get("source_url")
                index_map = raw.get("index_map", {})
            else:
                tracks = raw
                injected = []
                source_url = None
                index_map = {}

            with self.conn:
                self.conn.executemany(
                    "INSERT OR IGNORE INTO tracks (track_id, filename) VALUES (?, ?)",
                    tracks.items(),
                )
                self.conn.executemany(
                    "INSERT OR IGNORE INTO injected (track_id) VALUES (?)",
                    [(tid,) for tid in injected],
                )
                if source_url:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        ("source_url", source_url),
                    )
                if index_map:
                    self.conn.execute(
                        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                        ("index_map", json.dumps(index_map)),
                    )

            backup_path = self.output_dir / ".sync.json.bak"
            json_path.rename(backup_path)
        except Exception:
            pass

    def close(self):
        try:
            self.conn.close()
        except Exception:
            pass

    def flush(self):
        pass

    def get_source_url(self):
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'source_url'"
        ).fetchone()
        return row[0] if row else None

    def set_source_url(self, url):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("source_url", url),
            )

    def is_synced(self, track_id):
        row = self.conn.execute(
            "SELECT filename FROM tracks WHERE track_id = ?", (track_id,)
        ).fetchone()
        if row:
            local_file = self.output_dir / row[0]
            return local_file.exists()
        return False

    def mark_synced(self, track_id, filename):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO tracks (track_id, filename) VALUES (?, ?)",
                (track_id, filename),
            )

    def mark_injected(self, track_id, filename):
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO injected (track_id) VALUES (?)",
                (track_id,),
            )
        self.mark_synced(track_id, filename)

    def update_index_map(self, mapping):
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
                ("index_map", json.dumps(mapping)),
            )

    def get_index(self, track_id):
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = 'index_map'"
        ).fetchone()
        if row:
            try:
                index_map = json.loads(row[0])
                return index_map.get(track_id)
            except Exception:
                pass
        return None

    def get_filename(self, track_id):
        row = self.conn.execute(
            "SELECT filename FROM tracks WHERE track_id = ?", (track_id,)
        ).fetchone()
        return row[0] if row else None

    def cleanup(self, current_spotify_ids, dry_run=False, no_delete=False):
        deleted_files = []

        if no_delete:
            return []

        rows = self.conn.execute("SELECT track_id, filename FROM tracks").fetchall()
        injected_ids = set(
            r[0] for r in self.conn.execute("SELECT track_id FROM injected").fetchall()
        )

        ids_to_remove = []
        for track_id, filename in rows:
            if track_id not in current_spotify_ids and track_id not in injected_ids:
                local_file = self.output_dir / filename
                if local_file.exists():
                    deleted_files.append(filename)
                    if not dry_run:
                        try:
                            local_file.unlink()
                        except Exception:
                            pass

                if not dry_run:
                    ids_to_remove.append(track_id)

        if not dry_run and ids_to_remove:
            with self.conn:
                self.conn.executemany(
                    "DELETE FROM tracks WHERE track_id = ?",
                    [(tid,) for tid in ids_to_remove],
                )

        return deleted_files
