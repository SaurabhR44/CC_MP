#!/usr/bin/env python3
"""
Demonstration of Docksmith building and running the fire-alert container.

This mimics the real-world use case from the spec:
1. Build a fire-alert container image
2. List available images
3. Run the container
"""

import sys
import tempfile
from pathlib import Path


def setup_demo():
    """Create a realistic demo context for fire-alert application."""
    
    tmpdir = tempfile.mkdtemp()
    context_dir = Path(tmpdir)
    
    print(f"Creating fire-alert demo in {context_dir}")
    
    # Create Python application
    main_py = context_dir / "fire_alert.py"
    main_py.write_text("""\
#!/usr/bin/env python3
\"\"\"
Fire Alert Emergency Response Container

This container is triggered when fire is detected by an AI camera.
It performs emergency response actions.
\"\"\"

import os
import sys
from datetime import datetime


def main():
    alert_type = os.environ.get('ALERT_TYPE', 'unknown')
    location = os.environ.get('ALERT_LOCATION', 'Unknown Location')
    
    print("=" * 60)
    print("🚨 EMERGENCY ALERT 🚨")
    print("=" * 60)
    print(f"Type: {alert_type.upper()}")
    print(f"Location: {location}")
    print(f"Time: {datetime.now().isoformat()}")
    print(f"Working Directory: {os.getcwd()}")
    print("=" * 60)
    print()
    
    if alert_type == "fire":
        print(">>> ACTIVATING FIRE RESPONSE PROTOCOL")
        print()
        print("[ ] Sounding alarm...")
        print("[x] Alarm sounding")
        print()
        print("[ ] Logging incident...")
        print("[x] Incident logged to /var/log/fire_incidents.log")
        print()
        print("[ ] Triggering emergency services notification...")
        print("[x] Notification sent")
        print()
        print("[ ] Triggering sprinkler system...")
        print("[x] Sprinkler system activated")
        print()
        print(">>> FIRE RESPONSE COMPLETE")
        
    elif alert_type == "intruder":
        print(">>> ACTIVATING INTRUDER ALERT PROTOCOL")
        print()
        print("[x] Sounding security alarm")
        print("[x] Locking all doors")
        print("[x] Notifying authorities")
        print("[x] Recording video")
        print()
        print(">>> INTRUDER ALERT COMPLETE")
    
    else:
        print(f"Unknown alert type: {alert_type}")
    
    print()
    print("Container execution completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
""")
    
    # Create requirements.txt
    requirements = context_dir / "requirements.txt"
    requirements.write_text("# No external dependencies for this demo\n")
    
    # Create a configuration file
    config_sh = context_dir / "entrypoint.sh"
    config_sh.write_text("#!/bin/bash\n# Emergency alert system entrypoint\npython3 fire_alert.py\n")
    
    # Create Docksmithfile
    docksmithfile = context_dir / "Docksmithfile"
    docksmithfile.write_text("""\
# Fire Alert Container Image
FROM python-base:latest
WORKDIR /app/fire_alert
COPY . /app/fire_alert
RUN echo "Fire alert system installed successfully"
ENV ALERT_TYPE=fire
ENV ALERT_LOCATION=Building-A-Lab
CMD ["python3", "fire_alert.py"]
""")
    
    print("✓ Created fire-alert application files:")
    print(f"  - {main_py.name}")
    print(f"  - {requirements.name}")
    print(f"  - Docksmithfile")
    print()
    
    return context_dir


def run_demo():
    """Run the full Docksmith demo."""
    
    sys.path.insert(0, str(Path(__file__).parent))
    
    from docksmith import setup_docksmith_home, DOCKSMITH_HOME, cmd_build, cmd_images, cmd_run
    from manifest import Manifest, Config
    from parser import DocksmithfileParser
    from builder import BuildEngine
    import argparse
    
    # Setup
    setup_docksmith_home()
    print(f"Docksmith home: {DOCKSMITH_HOME}")
    print()
    
    # Create base image
    print("=" * 60)
    print("STEP 1: Create Base Image")
    print("=" * 60)
    
    base_manifest = Manifest(
        name="python-base",
        tag="latest",
        created="2026-03-13T00:00:00Z",
        config=Config(
            Env=[],
            Cmd=["/bin/python3"],
            WorkingDir="/",
        ),
        layers=[],
    )
    base_manifest.digest = base_manifest.compute_digest()
    images_dir = DOCKSMITH_HOME / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    base_manifest.save(images_dir / "python-base:latest.json")
    print("✓ Base image created: python-base:latest")
    print()
    
    # Create demo context
    print("=" * 60)
    print("STEP 2: Create Fire-Alert Application")
    print("=" * 60)
    
    context_dir = setup_demo()
    print()
    
    # Build image
    print("=" * 60)
    print("STEP 3: Build Fire-Alert Container Image")
    print("=" * 60)
    print()
    
    docksmithfile = context_dir / "Docksmithfile"
    parser = DocksmithfileParser(docksmithfile)
    instructions = parser.parse()
    
    builder = BuildEngine(DOCKSMITH_HOME, context_dir)
    image = builder.build("fire-alert:v1.0", instructions)
    
    print()
    print(f"✓ Image built successfully!")
    print(f"  Name: {image.name}")
    print(f"  Tag: {image.tag}")
    print(f"  Digest: {image.digest[:19]}...")
    print(f"  Layers: {len(image.layers)}")
    print()
    
    # List images
    print("=" * 60)
    print("STEP 4: List Available Images")
    print("=" * 60)
    
    images = sorted((images_dir).glob("*.json"))
    print(f"Total images: {len(images)}")
    print()
    print("REPOSITORY         TAG            DIGEST")
    print("-" * 60)
    for manifest_file in images:
        m = Manifest.load(manifest_file)
        digest_short = m.digest[:19] + "..." if m.digest else "unknown"
        print(f"{m.name:20} {m.tag:15} {digest_short}")
    print()
    
    # Summary
    print("=" * 60)
    print("DEMO COMPLETE")
    print("=" * 60)
    print()
    print("Built image: fire-alert:v1.0")
    print()
    print("To run the container, you would execute:")
    print("  $ docksmith run fire-alert:v1.0")
    print()
    print("The container would:")
    print("  1. Extract all layers to a temporary filesystem")
    print("  2. Isolate with Linux chroot/fork/exec")
    print("  3. Execute: python3 fire_alert.py")
    print("  4. Display emergency response output")
    print()


if __name__ == "__main__":
    try:
        run_demo()
    except Exception as e:
        print(f"\n✗ Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)
