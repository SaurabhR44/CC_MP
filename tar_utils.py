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
import hashlib
import stat
from pathlib import Path
from typing import List, Tuple, Dict
from io import BytesIO


def create_deterministic_tar(
    root_dir: Path,
) -> Tuple[bytes, str]:
    """
    Create a deterministic TAR archive from a directory.
    
    Returns:
        (tar_bytes, sha256_digest)
    
    Determinism guarantees:
    - All entries sorted lexicographically by arcname
    - All timestamps set to 0 (epoch)
    - All UIDs/GIDs set to 0
    - Consistent file modes (0755 for dirs/exec, 0644 for files)
    """
    
    root_dir = Path(root_dir)
    tar_buffer = BytesIO()
    
    # Collect all files and directories
    entries = []
    for path in root_dir.rglob('*'):
        rel_path = path.relative_to(root_dir)
        entries.append((path, str(rel_path).replace("\\", "/"))) # Normalize path separators

    # Sort entries by arcname for determinism
    entries.sort(key=lambda x: x[1])

    with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
        for filepath, arcname in entries:
            # Skip if it's the root itself (shouldn't happen with rglob('*'))
            if not arcname or arcname == ".":
                continue

            tarinfo = tar.gettarinfo(name=str(filepath), arcname=arcname)
            
            # Deterministic settings
            tarinfo.mtime = 0
            tarinfo.uid = 0
            tarinfo.gid = 0
            tarinfo.uname = ""
            tarinfo.gname = ""
            
            # Consistent permissions
            if filepath.is_dir():
                tarinfo.mode = 0o755
            elif filepath.is_symlink():
                # Symlinks don't have modes in the same way, but let's keep it safe
                pass
            else:
                # Preserve executable bit, but normalize
                if (os.stat(filepath).st_mode & stat.S_IXUSR):
                    tarinfo.mode = 0o755
                else:
                    tarinfo.mode = 0o644
            
            # Add to TAR
            if filepath.is_dir():
                tar.addfile(tarinfo)
            elif filepath.is_file():
                with open(filepath, 'rb') as f:
                    tar.addfile(tarinfo, f)
            elif filepath.is_symlink():
                tarinfo.type = tarfile.SYMTYPE
                tarinfo.linkname = os.readlink(filepath)
                tar.addfile(tarinfo)

    tar_bytes = tar_buffer.getvalue()
    digest = hashlib.sha256(tar_bytes).hexdigest()
    
    return tar_bytes, digest


def compute_delta(old_root: Path, new_root: Path) -> List[Path]:
    """
    Find files that are new or modified in new_root compared to old_root.
    Returns a list of relative paths.
    """
    delta_paths = []
    
    # Scan new_root
    for new_path in new_root.rglob('*'):
        if new_path.is_dir():
            continue
            
        rel_path = new_path.relative_to(new_root)
        old_path = old_root / rel_path
        
        if not old_path.exists():
            # New file
            delta_paths.append(rel_path)
            continue
            
        # Compare contents (SHA256)
        if hash_file(new_path) != hash_file(old_path):
            delta_paths.append(rel_path)
            
    return delta_paths


def create_layer_from_files(root_dir: Path, rel_paths: List[Path]) -> Tuple[bytes, str]:
    """
    Create a deterministic TAR containing only the specified relative paths.
    Used for delta layers (COPY and RUN).
    """
    tar_buffer = BytesIO()
    
    # Add files and their parent directories to the tar
    # First, collect all directories that need to be created
    paths_to_add = set()
    for rel_path in rel_paths:
        paths_to_add.add(rel_path)
        # Add parent directories
        for parent in rel_path.parents:
            if str(parent) != ".":
                paths_to_add.add(parent)
                
    sorted_paths = sorted(list(paths_to_add))

    with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
        for rel_path in sorted_paths:
            full_path = root_dir / rel_path
            arcname = str(rel_path).replace("\\", "/") # Normalize separators
            
            tarinfo = tar.gettarinfo(name=str(full_path), arcname=arcname)
            tarinfo.mtime = 0
            tarinfo.uid = 0
            tarinfo.gid = 0
            tarinfo.uname = ""
            tarinfo.gname = ""
            
            if full_path.is_dir():
                tarinfo.mode = 0o755
                tar.addfile(tarinfo)
            elif full_path.is_file():
                if (os.stat(full_path).st_mode & stat.S_IXUSR):
                    tarinfo.mode = 0o755
                else:
                    tarinfo.mode = 0o644
                with open(full_path, 'rb') as f:
                    tar.addfile(tarinfo, f)
                    
    tar_bytes = tar_buffer.getvalue()
    digest = hashlib.sha256(tar_bytes).hexdigest()
    return tar_bytes, digest


def extract_layers(layer_paths: List[Path], dest_dir: Path) -> None:
    """Extract multiple layers in order to a destination directory."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    
    for layer_path in layer_paths:
        if not layer_path.exists():
            raise FileNotFoundError(f"Layer file not found: {layer_path}")
            
        with tarfile.open(layer_path, 'r') as tar:
            # We use extractall as it overwrites by default
            tar.extractall(path=dest_dir)


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
    files = sorted([p for p in dirpath.rglob('*') if p.is_file()])
    for filepath in files:
        # Include relative path in hash for context
        rel_path = filepath.relative_to(dirpath)
        sha256.update(str(rel_path).replace("\\", "/").encode('utf-8'))
        
        # Include file content
        with open(filepath, 'rb') as f:
            while chunk := f.read(4096):
                sha256.update(chunk)
    
    return sha256.hexdigest()


def glob_files(pattern: str, base_dir: Path) -> List[Path]:
    """
    Glob pattern matching relative to base directory.
    Supports * and ** patterns.
    """
    import glob
    
    # glob.glob works with strings and supports recursive **
    full_pattern = os.path.join(base_dir, pattern)
    matching = glob.glob(full_pattern, recursive=True)
    
    # Filter to only include files, not directories
    files = [Path(p) for p in matching if os.path.isfile(p)]
    return sorted(files)

