#!/usr/bin/env python3
"""
End-to-end test of Docksmith build and runtime.
"""

import sys
import tempfile
import shutil
from pathlib import Path


def create_test_context(context_dir: Path):
    """Create a minimal test context with Docksmithfile."""
    
    # Create simple Python script
    main_py = context_dir / "main.py"
    main_py.write_text("""\
#!/usr/bin/env python3
import os
import sys

print("Hello from Docksmith container!")
print(f"Working directory: {os.getcwd()}")
print(f"Alert type: {os.environ.get('ALERT_TYPE', 'not set')}")
print("Test successful!")
""")
    
    # Create requirements.txt
    requirements = context_dir / "requirements.txt"
    requirements.write_text("# No dependencies for this test\n")
    
    # Create Docksmithfile
    docksmithfile = context_dir / "Docksmithfile"
    docksmithfile.write_text("""\
# Test Docksmithfile
FROM python-base:latest
WORKDIR /app
COPY . /app
RUN echo "Installing app..."
ENV ALERT_TYPE=test
CMD ["python3", "main.py"]
""")


def test_build():
    """Test docksmith build command."""
    print("=" * 60)
    print("TEST 1: docksmith build")
    print("=" * 60)
    
    # Import here to allow module loading
    sys.path.insert(0, str(Path(__file__).parent))
    
    from docksmith import setup_docksmith_home, DOCKSMITH_HOME
    from parser import DocksmithfileParser
    from builder import BuildEngine
    
    # Create base image first (required by test Docksmithfile)
    from manifest import Manifest, Config, Layer
    
    setup_docksmith_home()
    
    # Create base image
    base_manifest = Manifest(
        name="python-base",
        tag="latest",
        created="2026-03-13T00:00:00Z",
        config=Config(
            Env=[],
            Cmd=["/bin/sh"],
            WorkingDir="/",
        ),
        layers=[],
    )
    base_manifest.digest = base_manifest.compute_digest()
    images_dir = DOCKSMITH_HOME / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    base_manifest.save(images_dir / "python-base:latest.json")
    print("✓ Created base image: python-base:latest")
    
    # Create test context
    with tempfile.TemporaryDirectory() as tmpdir:
        context_dir = Path(tmpdir)
        create_test_context(context_dir)
        print(f"✓ Created test context: {context_dir}")
        
        # Parse Docksmithfile
        docksmithfile = context_dir / "Docksmithfile"
        parser = DocksmithfileParser(docksmithfile)
        instructions = parser.parse()
        print(f"✓ Parsed {len(instructions)} instructions")
        
        # Build image
        builder = BuildEngine(DOCKSMITH_HOME, context_dir)
        try:
            image = builder.build("test-app:v1", instructions)
            print(f"✓ Built image: {image.name}:{image.tag}")
            print(f"  Digest: {image.digest[:19]}...")
            print(f"  Layers: {len(image.layers)}")
            print("✓ BUILD TEST PASSED")
            return True
        except Exception as e:
            print(f"✗ Build failed: {e}")
            import traceback
            traceback.print_exc()
            return False


def test_parser():
    """Test Docksmithfile parser."""
    print("=" * 60)
    print("TEST 0: Docksmithfile Parser")
    print("=" * 60)
    
    sys.path.insert(0, str(Path(__file__).parent))
    from parser import DocksmithfileParser
    
    with tempfile.TemporaryDirectory() as tmpdir:
        context_dir = Path(tmpdir)
        create_test_context(context_dir)
        
        docksmithfile = context_dir / "Docksmithfile"
        parser = DocksmithfileParser(docksmithfile)
        instructions = parser.parse()
        
        print(f"Parsed {len(instructions)} instructions:")
        for instr in instructions:
            print(f"  Line {instr.line_number}: {instr.raw}")
        
        expected_types = ["FromInstruction", "WorkdirInstruction", "CopyInstruction", 
                         "RunInstruction", "EnvInstruction", "CmdInstruction"]
        actual_types = [type(i).__name__ for i in instructions]
        
        if actual_types == expected_types:
            print("✓ PARSER TEST PASSED")
            return True
        else:
            print(f"✗ Expected {expected_types}, got {actual_types}")
            return False


def test_manifest():
    """Test manifest creation and digest computation."""
    print("=" * 60)
    print("TEST 2: Manifest Serialization & Digest")
    print("=" * 60)
    
    sys.path.insert(0, str(Path(__file__).parent))
    from manifest import Manifest, Config, Layer
    
    manifest = Manifest(
        name="test",
        tag="v1",
        created="2026-03-13T00:00:00Z",
        config=Config(
            Env=["KEY=value"],
            Cmd=["python", "main.py"],
            WorkingDir="/app",
        ),
        layers=[
            Layer(digest="sha256:aaa", size=1024, createdBy="COPY . /app"),
            Layer(digest="sha256:bbb", size=2048, createdBy="RUN pip install"),
        ],
    )
    
    # Compute digest
    digest1 = manifest.compute_digest()
    print(f"Digest 1: {digest1[:19]}...")
    
    # Recompute - should be same (deterministic)
    digest2 = manifest.compute_digest()
    print(f"Digest 2: {digest2[:19]}...")
    
    if digest1 == digest2:
        print("✓ Digest is deterministic")
    else:
        print("✗ Digest not deterministic!")
        return False
    
    # Check JSON serialization
    manifest.digest = digest1
    with tempfile.TemporaryDirectory() as tmpdir:
        manifest_file = Path(tmpdir) / "test.json"
        manifest.save(manifest_file)
        
        # Reload
        reloaded = Manifest.load(manifest_file)
        
        if (reloaded.name == manifest.name and 
            reloaded.digest == manifest.digest and
            len(reloaded.layers) == len(manifest.layers)):
            print("✓ Load/save cycle works")
            print("✓ MANIFEST TEST PASSED")
            return True
        else:
            print("✗ Load/save failed!")
            return False


def main():
    """Run all tests."""
    print("\n" + "=" * 60)
    print("DOCKSMITH INTEGRATION TESTS")
    print("=" * 60 + "\n")
    
    results = []
    
    # Run tests
    results.append(("Parser", test_parser()))
    print()
    results.append(("Manifest", test_manifest()))
    print()
    results.append(("Build", test_build()))
    
    # Summary
    print("\n" + "=" * 60)
    print("TEST SUMMARY")
    print("=" * 60)
    
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"{name:20} {status}")
    
    all_passed = all(p for _, p in results)
    print("=" * 60)
    
    if all_passed:
        print("✓ All tests passed!")
        return 0
    else:
        print("✗ Some tests failed")
        return 1


if __name__ == "__main__":
    sys.exit(main())
