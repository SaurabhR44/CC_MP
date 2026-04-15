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
from pathlib import Path
from typing import Optional, List, Dict

from manifest import Manifest
from isolation import isolate_and_exec
from tar_utils import extract_layers


class ContainerRuntime:
    """Executes containers."""
    
    def __init__(self, docksmith_home: Path, manifest: Manifest):
        self.docksmith_home = docksmith_home
        self.manifest = manifest
        self.layers_dir = docksmith_home / "layers"
    
    def run(self, cmd_override: Optional[List[str]] = None, extra_env: Dict[str, str] = None) -> int:
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
        with tempfile.TemporaryDirectory() as container_root_tmp:
            container_root = Path(container_root_tmp)
            
            # Extract layers in order
            layer_paths = [self.layers_dir / L.digest.replace(':', '_') for L in self.manifest.layers]
            extract_layers(layer_paths, container_root)
            
            # Determine command to execute
            if cmd_override:
                cmd = cmd_override
            elif self.manifest.config.Cmd:
                cmd = self.manifest.config.Cmd
            else:
                raise RuntimeError("No command specified and no CMD in image")
            
            # Environment variables
            env_dict = {}
            # Base env from manifest
            for var in self.manifest.config.Env:
                if "=" in var:
                    key, value = var.split("=", 1)
                    env_dict[key] = value
            
            # Extra env from CLI (-e)
            if extra_env:
                env_dict.update(extra_env)
            
            # Execute container
            # runtime.run usually doesn't need to capture output (it's interactive)
            # but we return the exit code.
            exit_code, _, _ = isolate_and_exec(
                container_root,
                self.manifest.config.WorkingDir,
                env_dict,
                cmd,
                capture_output=False
            )
            
            return exit_code

