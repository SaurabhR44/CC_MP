"""
Build engine.

Orchestrates:
- Docksmithfile instruction execution
- Filesystem layer creation
- Deterministic build caching
- Manifest generation
"""

import hashlib
import json
import tempfile
import tarfile
import shutil
import glob
import os
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime
from dataclasses import dataclass, field

from parser import (
    Instruction,
    FromInstruction,
    CopyInstruction,
    RunInstruction,
    WorkdirInstruction,
    EnvInstruction,
    CmdInstruction,
)
from manifest import Manifest, Layer, Config
from tar_utils import create_deterministic_tar, glob_files, hash_file, hash_directory
from isolation import capture_process_output


@dataclass
class BuildState:
    """Tracks build state across instructions."""
    base_image_digest: str  # Digest of last layer (for cache key)
    working_dir: str = "/"
    env_vars: Dict[str, str] = field(default_factory=dict)
    cmd: Optional[List[str]] = None


class BuildEngine:
    """Executes build instructions."""
    
    def __init__(self, docksmith_home: Path, context_path: Path):
        self.docksmith_home = docksmith_home
        self.context_path = context_path
        self.images_dir = docksmith_home / "images"
        self.layers_dir = docksmith_home / "layers"
        self.cache_dir = docksmith_home / "cache"
    
    def build(self, image_tag: str, instructions: List[Instruction]) -> Manifest:
        """
        Execute build instructions and return final manifest.
        
        Implements:
        - Cache hit/miss detection
        - Layer generation
        - Deterministic caching with cascade rule
        """
        name, tag = self._parse_tag(image_tag)
        
        # Initialize build state
        state = None
        layers = []
        cache_cascade_hit = True
        
        # Execute each instruction
        for instr in instructions:
            if isinstance(instr, FromInstruction):
                # Load base image
                base_image_tag = f"{instr.base_image}:{instr.tag}"
                base_manifest_path = self.images_dir / f"{base_image_tag}.json"
                
                if not base_manifest_path.exists():
                    raise RuntimeError(f"Base image '{base_image_tag}' not found")
                
                base_manifest = Manifest.load(base_manifest_path)
                
                # Reuse base layers
                layers = list(base_manifest.layers)
                base_digest = layers[-1].digest if layers else base_manifest.digest
                
                state = BuildState(base_image_digest=base_digest)
            
            elif isinstance(instr, WorkdirInstruction):
                state.working_dir = instr.path
            
            elif isinstance(instr, EnvInstruction):
                state.env_vars[instr.key] = instr.value
            
            elif isinstance(instr, CmdInstruction):
                state.cmd = instr.exec
            
            elif isinstance(instr, CopyInstruction):
                # Check cache
                cache_key = self._compute_cache_key(
                    state.base_image_digest,
                    instr,
                    state.working_dir,
                    state.env_vars,
                    self.context_path,
                )
                
                if cache_cascade_hit and self._check_cache(cache_key):
                    cached_digest = self._get_cache(cache_key)
                    print(f"[CACHE HIT] {instr.raw}")
                    layer = self._find_layer(cached_digest)
                    layers.append(layer)
                    state.base_image_digest = cached_digest
                else:
                    print(f"[CACHE MISS] {instr.raw}")
                    cache_cascade_hit = False
                    
                    # Execute COPY
                    layer = self._execute_copy(instr, state.working_dir)
                    layers.append(layer)
                    state.base_image_digest = layer.digest
                    
                    # Store in cache
                    self._store_cache(cache_key, layer.digest)
            
            elif isinstance(instr, RunInstruction):
                # Check cache
                cache_key = self._compute_cache_key(
                    state.base_image_digest,
                    instr,
                    state.working_dir,
                    state.env_vars,
                    None,  # RUN doesn't have source files
                )
                
                if cache_cascade_hit and self._check_cache(cache_key):
                    cached_digest = self._get_cache(cache_key)
                    print(f"[CACHE HIT] {instr.raw}")
                    layer = self._find_layer(cached_digest)
                    layers.append(layer)
                    state.base_image_digest = cached_digest
                else:
                    print(f"[CACHE MISS] {instr.raw}")
                    cache_cascade_hit = False
                    
                    # Execute RUN
                    layer = self._execute_run(instr, state.working_dir, state.env_vars)
                    layers.append(layer)
                    state.base_image_digest = layer.digest
                    
                    # Store in cache
                    self._store_cache(cache_key, layer.digest)
        
        # Create manifest
        manifest = Manifest(
            name=name,
            tag=tag,
            created=datetime.utcnow().isoformat() + "Z",
            config=Config(
                Env=[f"{k}={v}" for k, v in state.env_vars.items()],
                Cmd=state.cmd,
                WorkingDir=state.working_dir,
            ),
            layers=layers,
        )
        
        # Compute and save manifest
        manifest.digest = manifest.compute_digest()
        manifest_path = self.images_dir / f"{image_tag}.json"
        manifest.save(manifest_path)
        
        return manifest
    
    def _parse_tag(self, tag: str) -> Tuple[str, str]:
        """Parse 'name:tag' format."""
        if ":" in tag:
            name, tag = tag.split(":", 1)
        else:
            name = tag
            tag = "latest"
        return name, tag
    
    def _compute_cache_key(
        self,
        prev_digest: str,
        instr: Instruction,
        working_dir: str,
        env_vars: Dict[str, str],
        context_path: Optional[Path],
    ) -> str:
        """
        Compute cache key for an instruction.
        
        Hash of:
        - previous layer digest
        - instruction text
        - working directory
        - environment variables (sorted)
        - COPY source file hashes (sorted)
        """
        key_parts = [
            prev_digest,
            instr.raw,
            working_dir,
        ]
        
        # Add sorted env vars
        for key in sorted(env_vars.keys()):
            key_parts.append(f"{key}={env_vars[key]}")
        
        # Add COPY source file hashes
        if isinstance(instr, CopyInstruction) and context_path:
            src_hashes = self._hash_copy_sources(instr.src, context_path)
            key_parts.extend(sorted(src_hashes))
        
        # Compute hash
        key_str = "\n".join(key_parts)
        return hashlib.sha256(key_str.encode('utf-8')).hexdigest()
    
    def _hash_copy_sources(self, pattern: str, context_path: Path) -> List[str]:
        """Hash all source files matching a glob pattern."""
        # Handle "." pattern (copy entire context tree)
        if pattern == ".":
            files = sorted([p for p in context_path.rglob('*') 
                           if p.is_file() and p.name != 'Docksmithfile'])
        else:
            files = glob_files(pattern, context_path)
        
        hashes = []
        for filepath in files:
            with open(filepath, 'rb') as f:
                file_hash = hashlib.sha256(f.read()).hexdigest()
                hashes.append(file_hash)
        
        return hashes
    
    def _check_cache(self, cache_key: str) -> bool:
        """Check if cache key exists."""
        cache_file = self.cache_dir / cache_key
        return cache_file.exists()
    
    def _get_cache(self, cache_key: str) -> str:
        """Get cached layer digest."""
        cache_file = self.cache_dir / cache_key
        with open(cache_file, 'r') as f:
            return f.read().strip()
    
    def _store_cache(self, cache_key: str, layer_digest: str) -> None:
        """Store cache entry."""
        cache_file = self.cache_dir / cache_key
        with open(cache_file, 'w') as f:
            f.write(layer_digest)
    
    def _find_layer(self, digest: str) -> Layer:
        """Find layer by digest."""
        layer_path = self.layers_dir / digest
        if layer_path.exists():
            size = layer_path.stat().st_size
            # Try to get the createdBy from somewhere (placeholder)
            return Layer(digest=digest, size=size, createdBy="<cached>")
        raise RuntimeError(f"Layer {digest} not found")
    
    def _execute_copy(self, instr: CopyInstruction, working_dir: str) -> Layer:
        """
        Execute COPY instruction.
        
        Process:
        1. Glob match source files from context
        2. Create temporary layer root
        3. Copy files preserving structure
        4. Create deterministic TAR
        5. Store TAR with SHA256 filename
        """
        
        # Find source files
        if instr.src == ".":
            # Copy entire context (except Docksmithfile)
            src_files = sorted([
                p for p in self.context_path.rglob('*')
                if p.is_file() and p.name != 'Docksmithfile'
            ])
        else:
            src_files = glob_files(instr.src, self.context_path)
        
        if not src_files:
            print(f"Warning: COPY pattern '{instr.src}' matched no files")
        
        # Create temporary layer root
        with tempfile.TemporaryDirectory() as temp_layer:
            temp_layer = Path(temp_layer)
            
            # Copy files to layer
            for src_file in src_files:
                # Compute relative path from context
                rel_path = src_file.relative_to(self.context_path)
                
                # Destination in layer (respecting WORKDIR and COPY dest)
                dest_in_layer = temp_layer / working_dir.lstrip('/') / instr.dest.lstrip('/') / rel_path
                dest_in_layer.parent.mkdir(parents=True, exist_ok=True)
                
                # Copy file
                shutil.copy2(src_file, dest_in_layer)
            
            # Create deterministic TAR
            tar_bytes, digest = create_deterministic_tar(temp_layer)
            
            # Store layer
            digest_full = f"sha256:{digest}"
            layer_path = self.layers_dir / digest_full
            with open(layer_path, 'wb') as f:
                f.write(tar_bytes)
            
            return Layer(
                digest=digest_full,
                size=len(tar_bytes),
                createdBy=instr.raw,
            )
    
    def _execute_run(self, instr: RunInstruction, working_dir: str, env_vars: Dict[str, str]) -> Layer:
        """
        Execute RUN instruction.
        
        Process:
        1. Extract base image layers to temporary root
        2. Execute command inside container (chroot)
        3. Capture filesystem changes
        4. Create delta TAR
        5. Store TAR with SHA256 filename
        """
        
        # Create temporary container root
        with tempfile.TemporaryDirectory() as container_root_tmp:
            container_root = Path(container_root_tmp)
            
            # Extract all previous layers to container root
            # (This is a simplified version - in real implementation we'd track layers)
            
            # Execute command inside container
            cmd = ['/bin/sh', '-c', instr.command]
            
            stdout, stderr, exit_code = capture_process_output(
                container_root,
                working_dir,
                env_vars,
                cmd,
            )
            
            if exit_code != 0:
                print(f"Warning: RUN command failed with exit code {exit_code}")
                print(f"stdout: {stdout}")
                print(f"stderr: {stderr}")
            
            # Create delta TAR from changes
            # For now, create empty layer (placeholder)
            tar_bytes = b''
            digest = hashlib.sha256(tar_bytes).hexdigest()
            
            # Store layer
            digest_full = f"sha256:{digest}"
            layer_path = self.layers_dir / digest_full
            with open(layer_path, 'wb') as f:
                f.write(tar_bytes if tar_bytes else b'')
            
            return Layer(
                digest=digest_full,
                size=len(tar_bytes),
                createdBy=instr.raw,
            )
