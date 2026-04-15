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
import time
from pathlib import Path

from manifest import Image, Manifest
from parser import DocksmithfileParser
from builder import BuildEngine
from runtime import ContainerRuntime
from tar_utils import create_deterministic_tar


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
    builder = BuildEngine(DOCKSMITH_HOME, context_path, no_cache=args.no_cache)
    try:
        builder.build(image_tag, instructions)
    except Exception as e:
        print(f"Build failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_images(args):
    """docksmith images"""
    setup_docksmith_home()
    
    images_dir = DOCKSMITH_HOME / "images"
    manifests = sorted(images_dir.glob("*.json"))
    
    if not manifests:
        print("REPOSITORY          TAG                 IMAGE ID            CREATED")
        return
    
    print(f"{'REPOSITORY':20} {'TAG':20} {'IMAGE ID':20} {'CREATED':20}")
    
    for manifest_file in manifests:
        try:
            manifest = Manifest.load(manifest_file)
            image_id = manifest.digest[7:19] # Trim 'sha256:' and take 12 chars
            print(f"{manifest.name:20} {manifest.tag:20} {image_id:20} {manifest.created:20}")
        except Exception:
            continue


def cmd_run(args):
    """docksmith run name:tag [cmd]"""
    setup_docksmith_home()
    
    image_tag = args.tag
    cmd_override = args.cmd  # Optional command override
    
    # Parse env overrides
    extra_env = {}
    if args.env:
        for item in args.env:
            if "=" in item:
                k, v = item.split("=", 1)
                extra_env[k] = v
            else:
                print(f"Warning: invalid env format '{item}', expected KEY=VALUE")

    # Find image manifest
    images_dir = DOCKSMITH_HOME / "images"
    safe_image_tag = image_tag.replace(":", "_")
    manifest_file = images_dir / f"{safe_image_tag}.json"
    
    if not manifest_file.exists():
        print(f"Error: image '{image_tag}' not found", file=sys.stderr)
        sys.exit(1)
    
    manifest = Manifest.load(manifest_file)
    
    # Execute container
    runtime = ContainerRuntime(DOCKSMITH_HOME, manifest)
    try:
        exit_code = runtime.run(cmd_override, extra_env=extra_env)
        sys.exit(exit_code)
    except Exception as e:
        print(f"Run failed: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_rmi(args):
    """docksmith rmi name:tag"""
    setup_docksmith_home()
    
    image_tag = args.tag
    images_dir = DOCKSMITH_HOME / "images"
    safe_image_tag = image_tag.replace(":", "_")
    manifest_file = images_dir / f"{safe_image_tag}.json"
    
    if not manifest_file.exists():
        print(f"Error: image '{image_tag}' not found", file=sys.stderr)
        sys.exit(1)
    
    manifest = Manifest.load(manifest_file)
    
    # Delete associated layers (spec: no reference counting, just delete them)
    for layer in manifest.layers:
        layer_file = DOCKSMITH_HOME / "layers" / layer.digest.replace(":", "_")
        if layer_file.exists():
            layer_file.unlink()
            
    # Delete manifest
    manifest_file.unlink()
    print(f"Deleted: {image_tag}")


def cmd_import_base(args):
    """docksmith import-base <name:tag> <tarball_path>"""
    setup_docksmith_home()
    
    import tempfile
    import shutil
    from manifest import Config
    
    image_tag = args.tag
    tar_path = Path(args.path).resolve()
    
    if ":" in image_tag:
        name, tag = image_tag.split(":", 1)
    else:
        name, tag = image_tag, "latest"
        
    if not tar_path.exists():
        print(f"Error: tarball '{tar_path}' not found", file=sys.stderr)
        sys.exit(1)

    print(f"Importing base image {image_tag} from {tar_path}...")
    
    # We need to compute the digest of the tarball to make it a layer
    tar_bytes = tar_path.read_bytes()
    import hashlib
    digest = hashlib.sha256(tar_bytes).hexdigest()
    digest_full = f"sha256:{digest}"
    
    # Save layer
    layer_file = DOCKSMITH_HOME / "layers" / digest_full.replace(":", "_")
    layer_file.write_bytes(tar_bytes)
    
    # Create manifest
    manifest = Manifest(
        name=name,
        tag=tag,
        created=datetime.utcnow().isoformat() + "Z",
        config=Config(Env=[], Cmd=["/bin/sh"], WorkingDir="/"),
        layers=[Layer(digest=digest_full, size=len(tar_bytes), createdBy="import")],
    )
    manifest.digest = manifest.compute_digest()
    
    safe_tag = image_tag.replace(":", "_")
    manifest.save(DOCKSMITH_HOME / "images" / f"{safe_tag}.json")
    print(f"Successfully imported {image_tag} ({digest_full[:19]}...)")


def main():
    parser = argparse.ArgumentParser(
        description="Docksmith: simplified Docker-like container system",
        prog="docksmith"
    )
    
    subparsers = parser.add_subparsers(dest="command", help="command to run")
    
    # docksmith build
    build_parser = subparsers.add_parser("build", help="build an image")
    build_parser.add_argument("-t", "--tag", required=True, help="tag (name:tag)")
    build_parser.add_argument("--no-cache", action="store_true", help="skip cache")
    build_parser.add_argument("context", help="build context directory")
    build_parser.set_defaults(func=cmd_build)
    
    # docksmith images
    images_parser = subparsers.add_parser("images", help="list images")
    images_parser.set_defaults(func=cmd_images)
    
    # docksmith run
    run_parser = subparsers.add_parser("run", help="run a container")
    run_parser.add_argument("tag", help="image tag (name:tag)")
    run_parser.add_argument("cmd", nargs="*", help="command to run (optional)", default=None)
    run_parser.add_argument("-e", "--env", action="append", help="environment variable (KEY=VALUE)")
    run_parser.set_defaults(func=cmd_run)
    
    # docksmith rmi
    rmi_parser = subparsers.add_parser("rmi", help="remove an image")
    rmi_parser.add_argument("tag", help="image tag (name:tag)")
    rmi_parser.set_defaults(func=cmd_rmi)
    
    # docksmith import-base
    import_parser = subparsers.add_parser("import-base", help="import a base image tarball")
    import_parser.add_argument("tag", help="tag (name:tag)")
    import_parser.add_argument("path", help="path to tarball")
    import_parser.set_defaults(func=cmd_import_base)
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        sys.exit(0)
    
    args.func(args)


if __name__ == "__main__":
    from datetime import datetime
    from manifest import Layer
    main()

