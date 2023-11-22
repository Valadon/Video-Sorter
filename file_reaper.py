from datetime import datetime
from pathlib import Path

def reap_files(target_dir: str, cutoff: datetime, commit=True) -> list[Path]:
    """
    Deletes any files or directories older than the cutoff datetime
    """
    deleted: Path = []
    target = Path(target_dir)
    for f in target.iterdir():
        last_modified = datetime.fromtimestamp(f.stat().st_mtime)
        if f.is_dir():
            reap_files(str(f.resolve()), cutoff)
            is_empty = not any(f.iterdir())
            if is_empty:
                f.rmdir()
        else:
            if last_modified < cutoff:
                deleted.append(f)

    if commit:
        for f in deleted:
            f.unlink()

    return deleted
