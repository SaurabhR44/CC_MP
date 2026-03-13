"""
Container runtime.

Implements:
- Filesystem assembly from layers
- Process isolation (fork/chroot/exec)
- Environment variable injection
- Container lifecycle
"""

import os
import sys
import tempfile
import tarfile
from pathlib import Path
from typing import Optional, List

from manifest import Manifest
from isolation import isolate_and_exec


class ContainerRuntime:
    """Executes containers."""
    
    def __init__(self, docksmith_home: Path, manifest: Manifest):
        self.docksmith_home = docksmith_home
        self.manifest = manifest
        self.layers_dir = docksmith_home / "layers"
    
    def run(self, cmd_override: Optional[List[str]] = None) -> int:
        """
        Run container.
        
        Steps:
        1. Create temporary container root
        2. Extract layers in order
        3. Apply config (ENV, WorkingDir)
        4. Isolate with fork/chroot/exec
        5. Execute command (or use CMD from config)
        """
        
        # Create temporary container root
        with tempfile.TemporaryDirectory() as container_root:
            container_root = Path(container_root)
            
            # Extract layers in order
            for layer in self.manifest.layers:
                self._extract_layer(layer.digest, container_root)
            
            # Determine command to execute
            if cmd_override:
                cmd = cmd_override
            else:
                cmd = self.manifest.config.Cmd or ["/bin/sh"]
            
            # Convert env vars list to dict
            env_dict = {}
            for var in self.manifest.config.Env:
                if "=" in var:
                    key, value = var.split("=", 1)
                    env_dict[key] = value
            
            # Execute container
            return isolate_and_exec(
                container_root,
                self.manifest.config.WorkingDir,
                env_dict,
                cmd,
            )
    
    def _extract_layer(self, digest: str, container_root: Path) -> None:
        """Extract layer TAR archive into container root."""
        layer_path = self.layers_dir / digest
        
        if not layer_path.exists():
            raise RuntimeError(f"Layer {digest} not found")
        
        # Handle empty TAR (from placeholder layers)
        if layer_path.stat().st_size == 0:
            return
        
        try:
            with tarfile.open(layer_path, 'r') as tar:
                tar.extractall(path=container_root, filter='data')
        except tarfile.ReadError:
            # Empty or malformed tar
            pass
        except tarfile.TarError as e:
            raise RuntimeError(f"Failed to extract layer {digest}: {e}")
