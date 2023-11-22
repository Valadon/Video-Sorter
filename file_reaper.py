from datetime import datetime
from pathlib import Path

# CURRENTLY UNUSED
def reap_files(target_dir: str, cutoff: datetime):
    """
    Deletes any files or directories older than the cutoff datetime
    """
    target = Path(target_dir)
    for file in target.iterdir():
        last_modified = datetime.fromtimestamp(file.stat().st_mtime)
        if file.is_dir():
            reap_files(str(file.resolve()), cutoff)
            is_empty = not any(file.iterdir())
            if is_empty:
                file.rmdir()
        else:
            if last_modified < cutoff:
                file.unlink()
