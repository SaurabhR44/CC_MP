"""
Docksmithfile parser.

Supported instructions:
- FROM <image>[:tag]
- COPY <src> <dest>
- RUN <command>
- WORKDIR <path>
- ENV <key>=<value>
- CMD ["exec","arg"]
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Union
import shlex


@dataclass
class Instruction:
    """Base class for all instructions."""
    line_number: int
    raw: str


@dataclass
class FromInstruction(Instruction):
    """FROM <image>[:tag]"""
    base_image: str
    tag: str = "latest"


@dataclass
class CopyInstruction(Instruction):
    """COPY <src> <dest>"""
    src: str  # Glob pattern
    dest: str


@dataclass
class RunInstruction(Instruction):
    """RUN <command>"""
    command: str  # Shell command


@dataclass
class WorkdirInstruction(Instruction):
    """WORKDIR <path>"""
    path: str


@dataclass
class EnvInstruction(Instruction):
    """ENV <key>=<value>"""
    key: str
    value: str


@dataclass
class CmdInstruction(Instruction):
    """CMD ["exec","arg"]"""
    exec: List[str]  # Executable and arguments


class DocksmithfileParser:
    """Parses Docksmithfile."""
    
    def __init__(self, path: Path):
        self.path = path
        self.lines = self._load_lines()
    
    def _load_lines(self) -> List[str]:
        """Load lines from Docksmithfile, stripping comments and whitespace."""
        with open(self.path, 'r') as f:
            lines = f.readlines()
        
        # Strip comments and trailing whitespace
        cleaned = []
        for line in lines:
            line = line.split('#')[0].rstrip()
            if line:
                cleaned.append(line)
        
        return cleaned
    
    def parse(self) -> List[Instruction]:
        """Parse all instructions."""
        instructions = []
        
        for line_num, line in enumerate(self.lines, start=1):
            parts = line.split(None, 1)
            if not parts:
                continue
            
            cmd = parts[0].upper()
            args = parts[1] if len(parts) > 1 else ""
            
            if cmd == "FROM":
                instr = self._parse_from(line_num, args)
            elif cmd == "COPY":
                instr = self._parse_copy(line_num, args)
            elif cmd == "RUN":
                instr = self._parse_run(line_num, args)
            elif cmd == "WORKDIR":
                instr = self._parse_workdir(line_num, args)
            elif cmd == "ENV":
                instr = self._parse_env(line_num, args)
            elif cmd == "CMD":
                instr = self._parse_cmd(line_num, args)
            else:
                raise SyntaxError(f"Line {line_num}: Unknown instruction '{cmd}'")
            
            instructions.append(instr)
        
        return instructions
    
    def _parse_from(self, line_num: int, args: str) -> FromInstruction:
        """FROM <image>[:tag]"""
        if not args.strip():
            raise SyntaxError(f"Line {line_num}: FROM requires an image name")
        
        parts = args.strip().split(":", 1)
        base_image = parts[0]
        tag = parts[1] if len(parts) > 1 else "latest"
        
        return FromInstruction(
            line_number=line_num,
            raw=f"FROM {args}",
            base_image=base_image,
            tag=tag,
        )
    
    def _parse_copy(self, line_num: int, args: str) -> CopyInstruction:
        """COPY <src> <dest>"""
        parts = args.strip().split()
        if len(parts) < 2:
            raise SyntaxError(f"Line {line_num}: COPY requires <src> and <dest>")
        
        src = parts[0]
        dest = parts[1]
        
        return CopyInstruction(
            line_number=line_num,
            raw=f"COPY {args}",
            src=src,
            dest=dest,
        )
    
    def _parse_run(self, line_num: int, args: str) -> RunInstruction:
        """RUN <command>"""
        if not args.strip():
            raise SyntaxError(f"Line {line_num}: RUN requires a command")
        
        return RunInstruction(
            line_number=line_num,
            raw=f"RUN {args}",
            command=args.strip(),
        )
    
    def _parse_workdir(self, line_num: int, args: str) -> WorkdirInstruction:
        """WORKDIR <path>"""
        if not args.strip():
            raise SyntaxError(f"Line {line_num}: WORKDIR requires a path")
        
        return WorkdirInstruction(
            line_number=line_num,
            raw=f"WORKDIR {args}",
            path=args.strip(),
        )
    
    def _parse_env(self, line_num: int, args: str) -> EnvInstruction:
        """ENV <key>=<value>"""
        if not args.strip() or "=" not in args:
            raise SyntaxError(f"Line {line_num}: ENV requires <key>=<value>")
        
        key, value = args.split("=", 1)
        key = key.strip()
        value = value.strip()
        
        return EnvInstruction(
            line_number=line_num,
            raw=f"ENV {args}",
            key=key,
            value=value,
        )
    
    def _parse_cmd(self, line_num: int, args: str) -> CmdInstruction:
        """CMD ["exec","arg"]"""
        if not args.strip().startswith('['):
            raise SyntaxError(f"Line {line_num}: CMD must be JSON array format")
        
        # Parse JSON array
        import json
        try:
            exec_list = json.loads(args.strip())
            if not isinstance(exec_list, list):
                raise ValueError("CMD must be a JSON array")
        except json.JSONDecodeError as e:
            raise SyntaxError(f"Line {line_num}: CMD JSON parse error: {e}")
        
        return CmdInstruction(
            line_number=line_num,
            raw=f"CMD {args}",
            exec=exec_list,
        )
