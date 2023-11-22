from datetime import datetime
from pathlib import Path

def reap_files(target_dir: str, cutoff: datetime) -> list[str]:
    """
    Deletes any files or directories older than the cutoff datetime
    """
    deleted_files: list[str] = []

    target = Path(target_dir)
    for f in target.iterdir():
        last_modified = datetime.fromtimestamp(f.stat().st_mtime)
        if f.is_dir():
            deleted_files.extend(reap_files(str(f.resolve()), cutoff))
            is_empty = not any(f.iterdir())
            if is_empty:
                f.rmdir()
                deleted_files.append(str(f))
        else:
            if last_modified < cutoff:
                f.unlink()
                deleted_files.append(str(f))

    return deleted_files
