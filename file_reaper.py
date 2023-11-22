from datetime import datetime
from pathlib import Path

def reap_files(target_dir: str, cutoff: datetime):
    target = Path(target_dir)
    for file in target.iterdir():
        last_modified = datetime.fromtimestamp(file.stat().st_mtime)
        dt = cutoff - last_modified
        if dt.total_seconds() > 0:
            if file.is_dir():
                reap_files(str(file.resolve()), cutoff)
                is_empty = not any(file.iterdir())
                if is_empty:
                    file.rmdir()
            else:
                file.unlink()