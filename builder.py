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
import time
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
from tar_utils import (
    create_layer_from_files, 
    extract_layers, 
    hash_file, 
    hash_directory, 
    glob_files,
    compute_delta
)
from isolation import isolate_and_exec


@dataclass
class BuildState:
    """Tracks build state across instructions."""
    base_image_digest: str  # Digest of last layer-producing instruction or base manifest
    working_dir: str = "/"
    env_vars: Dict[str, str] = field(default_factory=dict)
    cmd: Optional[List[str]] = None
    layers: List[Layer] = field(default_factory=list)


class BuildEngine:
    """Executes build instructions."""
    
    def __init__(self, docksmith_home: Path, context_path: Path, no_cache: bool = False):
        self.docksmith_home = docksmith_home
        self.context_path = context_path
        self.no_cache = no_cache
        self.images_dir = docksmith_home / "images"
        self.layers_dir = docksmith_home / "layers"
        self.cache_dir = docksmith_home / "cache"
    
    def build(self, image_tag: str, instructions: List[Instruction]) -> Manifest:
        """
        Execute build instructions and return final manifest.
        """
        name, tag = self._parse_tag(image_tag)
        
        state = None
        cache_cascade_hit = not self.no_cache
        total_start = time.time()
        
        for i, instr in enumerate(instructions):
            step_num = i + 1
            print(f"Step {step_num}/{len(instructions)} : {instr.raw}", end=" ", flush=True)
            
            step_start = time.time()
            
            if isinstance(instr, FromInstruction):
                # FROM - Base image setup
                state = self._handle_from(instr)
                print() # FROM has no cache status or timing
                continue
            
            if state is None:
                raise RuntimeError("First instruction must be FROM")

            # Instructions that don't produce layers
            if isinstance(instr, WorkdirInstruction):
                state.working_dir = instr.path
                print(f"({time.time() - step_start:.2f}s)")
                continue
            elif isinstance(instr, EnvInstruction):
                state.env_vars[instr.key] = instr.value
                print(f"({time.time() - step_start:.2f}s)")
                continue
            elif isinstance(instr, CmdInstruction):
                state.cmd = instr.exec
                print(f"({time.time() - step_start:.2f}s)")
                continue

            # Instructions that produce layers: COPY and RUN
            cache_key = self._compute_cache_key(state, instr)
            
            if cache_cascade_hit and self._check_cache(cache_key):
                cached_digest = self._get_cache(cache_key)
                layer = self._find_layer(cached_digest)
                if layer:
                    print(f"[CACHE HIT] ({time.time() - step_start:.2f}s)")
                    state.layers.append(layer)
                    state.base_image_digest = cached_digest
                    continue
            
            # Cache miss or cascade
            cache_cascade_hit = False
            print(f"[CACHE MISS]", end=" ", flush=True)
            
            if isinstance(instr, CopyInstruction):
                layer = self._execute_copy(instr, state)
            elif isinstance(instr, RunInstruction):
                layer = self._execute_run(instr, state)
            
            state.layers.append(layer)
            state.base_image_digest = layer.digest
            
            # Store in cache if not --no-cache
            if not self.no_cache:
                self._store_cache(cache_key, layer.digest)
            
            print(f"({time.time() - step_start:.2f}s)")

        total_duration = time.time() - total_start
        
        # Create manifest
        manifest = Manifest(
            name=name,
            tag=tag,
            created=datetime.utcnow().isoformat() + "Z",
            config=Config(
                Env=[f"{k}={v}" for k, v in sorted(state.env_vars.items())],
                Cmd=state.cmd,
                WorkingDir=state.working_dir,
            ),
            layers=state.layers,
        )
        
        # Save manifest
        manifest.digest = manifest.compute_digest()
        safe_image_tag = image_tag.replace(":", "_")
        manifest_path = self.images_dir / f"{safe_image_tag}.json"
        
        # Reproducible check: if all steps were hits, we should preserve "created" 
        # to keep manifest digest identical.
        if all(L.createdBy == "<cached>" for L in state.layers):
             # This is a bit simplified; real logic would load original manifest.
             pass

        manifest.save(manifest_path)
        print(f"Successfully built {manifest.digest} {image_tag} ({total_duration:.2f}s)")
        
        return manifest

    def _handle_from(self, instr: FromInstruction) -> BuildState:
        base_tag = f"{instr.base_image}:{instr.tag}"
        safe_tag = base_tag.replace(":", "_")
        manifest_path = self.images_dir / f"{safe_tag}.json"
        
        if not manifest_path.exists():
            raise RuntimeError(f"Base image '{base_tag}' not found")
            
        manifest = Manifest.load(manifest_path)
        return BuildState(
            base_image_digest=manifest.digest,
            working_dir=manifest.config.WorkingDir,
            env_vars={kv.split('=')[0]: kv.split('=')[1] for kv in manifest.config.Env},
            cmd=manifest.config.Cmd,
            layers=list(manifest.layers)
        )

    def _compute_cache_key(self, state: BuildState, instr: Instruction) -> str:
        key_parts = [
            state.base_image_digest,
            instr.raw,
            state.working_dir
        ]
        
        # Sorted ENV
        for k in sorted(state.env_vars.keys()):
            key_parts.append(f"{k}={state.env_vars[k]}")
            
        # Sorted COPY sources
        if isinstance(instr, CopyInstruction):
            src_files = glob_files(instr.src, self.context_path)
            for f in src_files:
                key_parts.append(f"{f.relative_to(self.context_path)}:{hash_file(f)}")
                
        return hashlib.sha256("\n".join(key_parts).encode('utf-8')).hexdigest()

    def _check_cache(self, key: str) -> bool:
        cache_file = self.cache_dir / key
        if not cache_file.exists():
            return False
        digest = cache_file.read_text().strip()
        layer_path = self.layers_dir / digest.replace(":", "_")
        return layer_path.exists()

    def _get_cache(self, key: str) -> str:
        return (self.cache_dir / key).read_text().strip()

    def _store_cache(self, key: str, digest: str) -> None:
        (self.cache_dir / key).write_text(digest)

    def _find_layer(self, digest: str) -> Optional[Layer]:
        layer_path = self.layers_dir / digest.replace(":", "_")
        if layer_path.exists():
            return Layer(digest=digest, size=layer_path.stat().st_size, createdBy="<cached>")
        return None

    def _execute_copy(self, instr: CopyInstruction, state: BuildState) -> Layer:
        src_files = glob_files(instr.src, self.context_path)
        if not src_files:
             print(f"Warning: COPY {instr.src} matched no files")
             
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            rel_paths = []
            
            for src_file in src_files:
                rel_path = src_file.relative_to(self.context_path)
                # If pattern was a specific file, rel_path might be just the name.
                # If pattern was a glob, rel_path is the matched path.
                
                # Dest logic: if dest ends in /, it's a directory.
                dest_path = Path(state.working_dir) / instr.dest.lstrip('/')
                
                # Full dest in tmp_dir
                # FIX: Convert dest_path to string before lstrip
                target = tmp_path / str(dest_path).lstrip('\\').lstrip('/') / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                
                import shutil
                shutil.copy2(src_file, target)
                rel_paths.append(target.relative_to(tmp_path))

            tar_bytes, digest = create_layer_from_files(tmp_path, rel_paths)
            return self._save_layer(tar_bytes, digest, instr.raw)

    def _execute_run(self, instr: RunInstruction, state: BuildState) -> Layer:
        with tempfile.TemporaryDirectory() as root_a_dir, \
             tempfile.TemporaryDirectory() as root_b_dir:
            
            root_a = Path(root_a_dir)
            root_b = Path(root_b_dir)
            
            # Assemble current filesystem in root_a
            layer_paths = [self.layers_dir / L.digest.replace(":", "_") for L in state.layers]
            extract_layers(layer_paths, root_a)
            
            # Copy root_a to root_b
            import shutil
            # This is slow, but necessary for delta capture without specialized FS
            # We use cp -a to preserve everything
            os.system(f"cp -a {root_a}/. {root_b}/")
            
            # Run command in root_b
            cmd = ["/bin/sh", "-c", instr.command]
            exit_code, stdout, stderr = isolate_and_exec(
                root_b, 
                state.working_dir, 
                state.env_vars, 
                cmd, 
                capture_output=True
            )
            
            if exit_code != 0:
                print(f"\nCommand failed exit {exit_code}")
                if stdout: print(f"STDOUT: {stdout}")
                if stderr: print(f"STDERR: {stderr}")
                raise RuntimeError(f"RUN failed: {instr.command}")

            # Compute delta
            delta_rel_paths = compute_delta(root_a, root_b)
            tar_bytes, digest = create_layer_from_files(root_b, delta_rel_paths)
            return self._save_layer(tar_bytes, digest, instr.raw)

    def _save_layer(self, tar_bytes: bytes, digest: str, created_by: str) -> Layer:
        digest_full = f"sha256:{digest}"
        layer_file = self.layers_dir / digest_full.replace(":", "_")
        layer_file.write_bytes(tar_bytes)
        return Layer(digest=digest_full, size=len(tar_bytes), createdBy=created_by)

    def _parse_tag(self, tag: str) -> Tuple[str, str]:
        if ":" in tag:
            return tuple(tag.split(":", 1))
        return tag, "latest"

