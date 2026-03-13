"""
Image manifest and layer definitions.

Manifest format (JSON):
{
  "name": "myapp",
  "tag": "latest",
  "digest": "sha256:<hash>",
  "created": "<ISO-8601>",
  "config": {
    "Env": ["KEY=value"],
    "Cmd": ["python", "main.py"],
    "WorkingDir": "/app"
  },
  "layers": [
    {"digest": "sha256:aaa...", "size": 2048, "createdBy": "COPY . /app"},
    {"digest": "sha256:bbb...", "size": 4096, "createdBy": "RUN pip install"}
  ]
}
"""

import json
import hashlib
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional


@dataclass
class Layer:
    """A single filesystem layer (TAR archive)."""
    digest: str  # SHA256 of tar bytes (content-addressed)
    size: int  # TAR file size in bytes
    createdBy: str  # Instruction that created this layer (e.g., "COPY . /app")


@dataclass
class Config:
    """Container runtime configuration."""
    Env: List[str] = field(default_factory=list)  # ["KEY=value", ...]
    Cmd: Optional[List[str]] = None  # ["python", "main.py"]
    WorkingDir: str = "/"


@dataclass
class Manifest:
    """Image manifest."""
    name: str
    tag: str
    created: str  # ISO-8601 timestamp
    config: Config
    layers: List[Layer] = field(default_factory=list)
    digest: str = ""  # Computed by compute_digest()
    
    def to_dict(self) -> dict:
        """Convert to dictionary (for JSON serialization)."""
        return {
            "name": self.name,
            "tag": self.tag,
            "digest": self.digest,
            "created": self.created,
            "config": {
                "Env": self.config.Env,
                "Cmd": self.config.Cmd,
                "WorkingDir": self.config.WorkingDir,
            },
            "layers": [
                {
                    "digest": layer.digest,
                    "size": layer.size,
                    "createdBy": layer.createdBy,
                }
                for layer in self.layers
            ],
        }
    
    def compute_digest(self) -> str:
        """
        Compute SHA256 of manifest with digest="".
        
        Process:
        1. Serialize to JSON with digest=""
        2. Compute SHA256 of serialization
        3. Return "sha256:<hex>"
        """
        data = self.to_dict()
        data["digest"] = ""
        
        # Serialize deterministically (sorted keys)
        json_str = json.dumps(data, sort_keys=True, separators=(',', ':'))
        
        # Compute hash
        hash_obj = hashlib.sha256(json_str.encode('utf-8'))
        return f"sha256:{hash_obj.hexdigest()}"
    
    def save(self, path: Path) -> None:
        """Save manifest to JSON file."""
        # Compute digest before saving
        if not self.digest:
            self.digest = self.compute_digest()
        
        path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
    
    @staticmethod
    def load(path: Path) -> "Manifest":
        """Load manifest from JSON file."""
        with open(path, 'r') as f:
            data = json.load(f)
        
        config = Config(
            Env=data["config"].get("Env", []),
            Cmd=data["config"].get("Cmd"),
            WorkingDir=data["config"].get("WorkingDir", "/"),
        )
        
        layers = [
            Layer(
                digest=layer_data["digest"],
                size=layer_data["size"],
                createdBy=layer_data["createdBy"],
            )
            for layer_data in data.get("layers", [])
        ]
        
        return Manifest(
            name=data["name"],
            tag=data["tag"],
            digest=data.get("digest", ""),
            created=data["created"],
            config=config,
            layers=layers,
        )


@dataclass
class Image:
    """An image in the local store."""
    manifest: Manifest
    path: Path  # Path to manifest JSON file
