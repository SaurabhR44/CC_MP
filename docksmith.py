#!/usr/bin/env python3
"""
Docksmith: A simplified Docker-like container build and runtime system.

Implements:
- Docksmithfile parsing (FROM, COPY, RUN, WORKDIR, ENV, CMD)
- Content-addressed filesystem layers
- Deterministic build caching
- Linux-based container isolation (fork/chroot/exec)
"""

import sys
import os
import argparse
from pathlib import Path

# Import submodules (to be created)
from manifest import Image, Manifest
from parser import DocksmithfileParser
from builder import BuildEngine
from runtime import ContainerRuntime


DOCKSMITH_HOME = Path.home() / ".docksmith"


def setup_docksmith_home():
    """Initialize ~/.docksmith directory structure."""
    (DOCKSMITH_HOME / "images").mkdir(parents=True, exist_ok=True)
    (DOCKSMITH_HOME / "layers").mkdir(parents=True, exist_ok=True)
    (DOCKSMITH_HOME / "cache").mkdir(parents=True, exist_ok=True)


def cmd_build(args):
    """docksmith build -t name:tag <context>"""
    setup_docksmith_home()
    
    image_tag = args.tag
    context_path = Path(args.context).resolve()
    
    if not context_path.exists():
        print(f"Error: context directory '{context_path}' does not exist", file=sys.stderr)
        sys.exit(1)
    
    docksmithfile = context_path / "Docksmithfile"
    if not docksmithfile.exists():
        print(f"Error: Docksmithfile not found in {context_path}", file=sys.stderr)
        sys.exit(1)
    
    # Parse Docksmithfile
    parser = DocksmithfileParser(docksmithfile)
    instructions = parser.parse()
    
    # Execute build
    builder = BuildEngine(DOCKSMITH_HOME, context_path)
    image = builder.build(image_tag, instructions)
    
    print(f"Successfully built image: {image_tag}")


def cmd_images(args):
    """docksmith images"""
    setup_docksmith_home()
    
    images_dir = DOCKSMITH_HOME / "images"
    if not images_dir.exists() or not list(images_dir.glob("*.json")):
        print("No images found")
        return
    
    print("REPOSITORY    TAG        DIGEST")
    print("-" * 70)
    
    for manifest_file in sorted(images_dir.glob("*.json")):
        manifest = Manifest.load(manifest_file)
        name = manifest.name
        tag = manifest.tag
        digest = manifest.digest[:19] + "..."  # Shorten for display
        print(f"{name:20} {tag:15} {digest}")


def cmd_run(args):
    """docksmith run name:tag [cmd]"""
    setup_docksmith_home()
    
    image_tag = args.tag
    cmd_override = args.cmd  # Optional command override
    
    # Find image manifest
    images_dir = DOCKSMITH_HOME / "images"
    manifest_file = images_dir / f"{image_tag}.json"
    
    if not manifest_file.exists():
        print(f"Error: image '{image_tag}' not found", file=sys.stderr)
        sys.exit(1)
    
    manifest = Manifest.load(manifest_file)
    
    # Execute container
    runtime = ContainerRuntime(DOCKSMITH_HOME, manifest)
    exit_code = runtime.run(cmd_override)
    
    sys.exit(exit_code)


def cmd_rmi(args):
    """docksmith rmi name:tag"""
    setup_docksmith_home()
    
    image_tag = args.tag
    images_dir = DOCKSMITH_HOME / "images"
    manifest_file = images_dir / f"{image_tag}.json"
    
    if not manifest_file.exists():
        print(f"Error: image '{image_tag}' not found", file=sys.stderr)
        sys.exit(1)
    
    manifest = Manifest.load(manifest_file)
    
    # Delete manifest
    manifest_file.unlink()
    
    # TODO: Delete associated layers if no other image uses them
    print(f"Deleted image: {image_tag}")


def main():
    parser = argparse.ArgumentParser(
        description="Docksmith: simplified Docker-like container system",
        prog="docksmith"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="command to run")
    
    # docksmith build
    build_parser = subparsers.add_parser("build", help="build an image")
    build_parser.add_argument("-t", "--tag", required=True, help="tag (name:tag)")
    build_parser.add_argument("context", help="build context directory")
    build_parser.set_defaults(func=cmd_build)
    
    # docksmith images
    images_parser = subparsers.add_parser("images", help="list images")
    images_parser.set_defaults(func=cmd_images)
    
    # docksmith run
    run_parser = subparsers.add_parser("run", help="run a container")
    run_parser.add_argument("tag", help="image tag (name:tag)")
    run_parser.add_argument("cmd", nargs="*", help="command to run (optional)", default=None)
    run_parser.set_defaults(func=cmd_run)
    
    # docksmith rmi
    rmi_parser = subparsers.add_parser("rmi", help="remove an image")
    rmi_parser.add_argument("tag", help="image tag (name:tag)")
    rmi_parser.set_defaults(func=cmd_rmi)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(0)
    
    args.func(args)


if __name__ == "__main__":
    main()
