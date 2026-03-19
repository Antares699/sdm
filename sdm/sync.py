import json
from pathlib import Path


class SyncManager:
    def __init__(self, output_dir):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.sync_file = self.output_dir / ".sync.json"
        self.data = {
            "_version": 2,
            "source_url": None,
            "tracks": {},
            "injected": [],
            "index_map": {},
        }
        self.load()

    def load(self):
        if self.sync_file.exists():
            try:
                with open(self.sync_file, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                    if raw.get("_version") == 2:
                        self.data = raw

                        if "index_map" not in self.data:
                            self.data["index_map"] = {}
                        if "source_url" not in self.data:
                            self.data["source_url"] = None
                    else:
                        self.data["tracks"] = raw
                        self.data["injected"] = []
                        self.data["index_map"] = {}
                        self.data["source_url"] = None
                        self.save()
            except Exception:
                pass

    def save(self):
        with open(self.sync_file, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=4)

    def get_source_url(self):
        return self.data.get("source_url")

    def set_source_url(self, url):
        self.data["source_url"] = url
        self.save()

    def is_synced(self, track_id):
        if track_id in self.data["tracks"]:
            local_file = self.output_dir / self.data["tracks"][track_id]
            return local_file.exists()
        return False

    def mark_synced(self, track_id, filename):
        self.data["tracks"][track_id] = filename
        self.save()

    def mark_injected(self, track_id, filename):
        if track_id not in self.data["injected"]:
            self.data["injected"].append(track_id)
        self.mark_synced(track_id, filename)

    def update_index_map(self, mapping):
        self.data["index_map"] = mapping
        self.save()

    def get_index(self, track_id):
        return self.data.get("index_map", {}).get(track_id)

    def cleanup(self, current_spotify_ids, dry_run=False, no_delete=False):
        deleted_files = []
        ids_to_remove = []

        if no_delete:
            return []

        for track_id, filename in list(self.data["tracks"].items()):
            if (
                track_id not in current_spotify_ids
                and track_id not in self.data["injected"]
            ):
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

        if not dry_run:
            for track_id in ids_to_remove:
                del self.data["tracks"][track_id]
            if ids_to_remove:
                self.save()

        return deleted_files
