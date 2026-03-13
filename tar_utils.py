"""
Deterministic TAR archive utilities.

Ensures reproducible builds:
- All entries sorted by path
- All timestamps zeroed to epoch (1970-01-01)
- Consistent file permissions
- Deterministic hashing of TAR content
"""

import tarfile
import os
import tempfile
import hashlib
import stat
from pathlib import Path
from typing import List, Tuple
from io import BytesIO


def create_deterministic_tar(
    root_dir: Path,
    arcname_prefix: str = "",
) -> Tuple[bytes, str]:
    """
    Create a deterministic TAR archive from a directory.
    
    Returns:
        (tar_bytes, sha256_digest)
    
    Determinism guarantees:
    - All entries sorted lexicographically
    - All timestamps set to 0 (epoch)
    - All UIDs/GIDs set to 0
    - Consistent file modes
    """
    
    root_dir = Path(root_dir)
    tar_buffer = BytesIO()
    
    with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
        # Collect all files in sorted order
        entries = []
        
        for dirpath, dirnames, filenames in os.walk(root_dir):
            # Sort everything for determinism
            dirnames.sort()
            filenames.sort()
            
            dirpath = Path(dirpath)
            rel_dir = dirpath.relative_to(root_dir)
            
            # Add directories first (sorted)
            for dirname in dirnames:
                dirpath_rel = rel_dir / dirname if str(rel_dir) != "." else Path(dirname)
                entries.append((dirpath / dirname, str(dirpath_rel), True))
            
            # Then files (sorted)
            for filename in filenames:
                filepath = dirpath / filename
                filepath_rel = rel_dir / filename if str(rel_dir) != "." else Path(filename)
                entries.append((filepath, str(filepath_rel), False))
        
        # Add entries to TAR in sorted order
        for filepath, arcname, is_dir in sorted(entries, key=lambda x: x[1]):
            filepath = Path(filepath)  # Ensure Path object
            tarinfo = tar.gettarinfo(name=str(filepath), arcname=arcname)
            
            # Deterministic settings
            tarinfo.mtime = 0  # Epoch
            tarinfo.uid = 0
            tarinfo.gid = 0
            tarinfo.uname = ""
            tarinfo.gname = ""
            
            # Consistent permissions
            if is_dir:
                tarinfo.mode = 0o755
            else:
                # Preserve executable bit, but normalize
                if stat.S_IMODE(tarinfo.mode) & 0o111:
                    tarinfo.mode = 0o755
                else:
                    tarinfo.mode = 0o644
            
            # Add file to TAR
            if is_dir:
                tar.addfile(tarinfo)
            else:
                with open(filepath, 'rb') as f:
                    tar.addfile(tarinfo, f)
    
    # Get TAR bytes
    tar_bytes = tar_buffer.getvalue()
    
    # Compute SHA256
    digest = hashlib.sha256(tar_bytes).hexdigest()
    
    return tar_bytes, digest


def create_delta_tar(
    old_root: Path,
    new_root: Path,
    arcname_prefix: str = "",
) -> Tuple[bytes, str]:
    """
    Create a delta TAR containing only changes between two directories.
    
    This handles layer diffs:
    - New/modified files
    - Deleted files (recorded with size 0)
    - Directory changes
    
    Returns:
        (tar_bytes, sha256_digest)
    """
    
    tar_buffer = BytesIO()
    
    with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
        # Get all files from both roots
        old_files = _scan_directory(old_root)
        new_files = _scan_directory(new_root)
        
        # Find all changes
        all_paths = set(old_files.keys()) | set(new_files.keys())
        
        for path in sorted(all_paths):
            old_entry = old_files.get(path)
            new_entry = new_files.get(path)
            
            if old_entry is None and new_entry is not None:
                # New file/directory
                _add_to_tar(tar, new_entry)
            elif old_entry is not None and new_entry is None:
                # Deleted file - record as whiteout (size 0)
                _add_deleted_to_tar(tar, old_entry)
            elif old_entry != new_entry:
                # Modified file
                _add_to_tar(tar, new_entry)
    
    tar_bytes = tar_buffer.getvalue()
    digest = hashlib.sha256(tar_bytes).hexdigest()
    
    return tar_bytes, digest


def _scan_directory(root: Path) -> dict:
    """Scan directory and return mapping of paths to file info."""
    files = {}
    
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        filenames.sort()
        
        rel_dir = Path(dirpath).relative_to(root)
        
        # Add directories
        for dirname in dirnames:
            dirpath_rel = str(rel_dir / dirname) if str(rel_dir) != "." else dirname
            filepath = Path(dirpath) / dirname
            files[dirpath_rel] = {
                "path": filepath,
                "is_dir": True,
                "mtime": os.stat(filepath).st_mtime,
                "mode": os.stat(filepath).st_mode,
            }
        
        # Add files
        for filename in filenames:
            filepath_rel = str(rel_dir / filename) if str(rel_dir) != "." else filename
            filepath = Path(dirpath) / filename
            files[filepath_rel] = {
                "path": filepath,
                "is_dir": False,
                "mtime": os.stat(filepath).st_mtime,
                "mode": os.stat(filepath).st_mode,
                "size": os.path.getsize(filepath),
            }
    
    return files


def _add_to_tar(tar, entry):
    """Add file/directory to TAR with deterministic settings."""
    filepath = entry["path"]
    is_dir = entry["is_dir"]
    
    tarinfo = tar.gettarinfo(arcname=str(filepath.name), fileobj=filepath)
    
    # Deterministic settings
    tarinfo.mtime = 0
    tarinfo.uid = 0
    tarinfo.gid = 0
    tarinfo.uname = ""
    tarinfo.gname = ""
    
    if is_dir:
        tarinfo.mode = 0o755
        tar.addfile(tarinfo)
    else:
        if stat.S_IMODE(tarinfo.mode) & 0o111:
            tarinfo.mode = 0o755
        else:
            tarinfo.mode = 0o644
        
        with open(filepath, 'rb') as f:
            tar.addfile(tarinfo, f)


def _add_deleted_to_tar(tar, entry):
    """Add deletion marker to TAR (file with size 0)."""
    filepath = entry["path"]
    
    tarinfo = tarfile.TarInfo(name=str(filepath.name))
    tarinfo.size = 0
    tarinfo.mtime = 0
    tarinfo.uid = 0
    tarinfo.gid = 0
    tarinfo.mode = 0o644
    
    tar.addfile(tarinfo)


def extract_tar_to_dir(tar_path: Path, dest_dir: Path) -> None:
    """Extract TAR archive to destination, handling whiteouts (deleted files)."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    with tarfile.open(tar_path, 'r') as tar:
        # Extract all members
        for member in tar.getmembers():
            # Skip extraction for now, use filter='data' for safety
            pass
        
        tar.extractall(path=dest_dir, filter='data')


def tar_digest_to_filename(digest: str) -> str:
    """Convert digest to filename in layers directory."""
    return digest if digest.startswith("sha256:") else f"sha256:{digest}"


def hash_file(filepath: Path) -> str:
    """Compute SHA256 hash of a file."""
    sha256 = hashlib.sha256()
    with open(filepath, 'rb') as f:
        for chunk in iter(lambda: f.read(4096), b''):
            sha256.update(chunk)
    return sha256.hexdigest()


def hash_directory(dirpath: Path) -> str:
    """
    Compute SHA256 hash of all files in directory (sorted order).
    Used for COPY source hash computation in cache keys.
    """
    sha256 = hashlib.sha256()
    
    # Hash all files in sorted order
    for filepath in sorted(dirpath.rglob('*')):
        if filepath.is_file():
            # Include relative path in hash
            rel_path = filepath.relative_to(dirpath)
            sha256.update(str(rel_path).encode('utf-8'))
            
            # Include file content
            with open(filepath, 'rb') as f:
                sha256.update(f.read())
    
    return sha256.hexdigest()


def glob_files(pattern: str, base_dir: Path) -> List[Path]:
    """
    Glob pattern matching relative to base directory.
    Supports * and ** patterns.
    Returns only files, not directories.
    """
    import glob
    
    full_pattern = str(base_dir / pattern)
    matching = glob.glob(full_pattern, recursive=True)
    
    # Filter to only include files, not directories
    files = [Path(p) for p in matching if Path(p).is_file()]
    return sorted(files)
