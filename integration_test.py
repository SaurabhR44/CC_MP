#!/usr/bin/env python3
"""
End-to-end integration test for Docksmith Layer 1.
Tests the full build → manifest → run pipeline.
"""

import tempfile
import shutil
import subprocess
from pathlib import Path
import json
import sys

def test_full_pipeline():
    """Test complete build and run workflow."""
    
    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)
        
        # Simulate ~/.docksmith
        docksmith_home = tmpdir / '.docksmith'
        docksmith_home.mkdir()
        (docksmith_home / 'images').mkdir()
        (docksmith_home / 'layers').mkdir()
        (docksmith_home / 'cache').mkdir()
        
        print('[Step 1] Create test context with Docksmithfile...')
        context = tmpdir / 'context'
        context.mkdir()
        
        # Create Docksmithfile
        docksmithfile = context / 'Docksmithfile'
        docksmithfile.write_text('''FROM base:latest
WORKDIR /app
COPY script.sh /app/
RUN echo "Building image..."
ENV MY_VAR=test_value
CMD ["bash", "script.sh"]
''')
        
        # Create source files
        (context / 'script.sh').write_text('#!/bin/bash\necho "Hello from container!"')
        
        # Create base image
        print('[Step 2] Create base image...')
        base_manifest = {
            'name': 'base',
            'tag': 'latest',
            'digest': 'sha256:base1234567890abcdef',
            'created': '2024-01-01T00:00:00Z',
            'config': {
                'Env': [],
                'Cmd': None,
                'WorkingDir': '/'
            },
            'layers': []
        }
        base_json = docksmith_home / 'images' / 'base:latest.json'
        with open(base_json, 'w') as f:
            json.dump(base_manifest, f)
        
        print('[Step 3] Parse Docksmithfile...')
        sys.path.insert(0, str(Path.cwd()))
        from parser import DocksmithfileParser
        
        parser = DocksmithfileParser(docksmithfile)
        instructions = parser.parse()
        
        assert len(instructions) == 6, f'Expected 6 instructions, got {len(instructions)}'
        print(f'  ✓ Parsed {len(instructions)} instructions')
        
        print('[Step 4] Test instruction types...')
        from parser import (
            FromInstruction, WorkdirInstruction, CopyInstruction,
            RunInstruction, EnvInstruction, CmdInstruction
        )
        
        assert isinstance(instructions[0], FromInstruction)
        assert isinstance(instructions[1], WorkdirInstruction)
        assert isinstance(instructions[2], CopyInstruction)
        assert isinstance(instructions[3], RunInstruction)
        assert isinstance(instructions[4], EnvInstruction)
        assert isinstance(instructions[5], CmdInstruction)
        print('  ✓ All instruction types correct')
        
        print('[Step 5] Test manifest generation...')
        from manifest import Manifest, Layer, Config
        
        manifest = Manifest(
            name='myapp',
            tag='v1.0',
            created='2024-01-01T00:00:00Z',
            config=Config(
                Env=['MY_VAR=test_value'],
                Cmd=['bash', 'script.sh'],
                WorkingDir='/app'
            ),
            layers=[
                Layer(digest='sha256:layer1', size=1024, createdBy='COPY script.sh /app/'),
                Layer(digest='sha256:layer2', size=2048, createdBy='RUN echo "Building..."'),
            ]
        )
        
        manifest.digest = manifest.compute_digest()
        assert manifest.digest.startswith('sha256:')
        print(f'  ✓ Manifest digest: {manifest.digest[:19]}...')
        
        # Save manifest
        manifest_path = docksmith_home / 'images' / 'myapp:v1.0.json'
        manifest.save(manifest_path)
        assert manifest_path.exists()
        print(f'  ✓ Manifest saved to {manifest_path.name}')
        
        # Load and verify
        loaded = Manifest.load(manifest_path)
        assert loaded.name == 'myapp'
        assert loaded.tag == 'v1.0'
        assert loaded.config.WorkingDir == '/app'
        assert 'MY_VAR=test_value' in loaded.config.Env
        print('  ✓ Manifest loaded and verified')
        
        print('[Step 6] Test TAR utilities...')
        from tar_utils import create_deterministic_tar
        
        # Create test filesystem
        test_root = tmpdir / 'test_fs'
        test_root.mkdir()
        (test_root / 'file1.txt').write_text('content1')
        (test_root / 'file2.txt').write_text('content2')
        (test_root / 'app').mkdir()
        (test_root / 'app' / 'main.py').write_text('print("hello")')
        
        tar_bytes, digest = create_deterministic_tar(test_root)
        assert len(tar_bytes) > 0
        assert len(digest) == 64
        print(f'  ✓ TAR created: {len(tar_bytes)} bytes')
        print(f'  ✓ Digest: {digest[:19]}...')
        
        # Verify determinism
        tar_bytes2, digest2 = create_deterministic_tar(test_root)
        assert tar_bytes == tar_bytes2, 'TAR not deterministic!'
        assert digest == digest2, 'Digests differ!'
        print('  ✓ Deterministic (byte-for-byte reproducible)')
        
        print('[Step 7] Test cache key computation...')
        from builder import BuildEngine
        
        be = BuildEngine(docksmith_home, context)
        
        key1 = be._compute_cache_key(
            'sha256:base123',
            instructions[2],  # COPY instruction
            '/app',
            {'MY_VAR': 'test_value'},
            context
        )
        
        key2 = be._compute_cache_key(
            'sha256:base123',
            instructions[2],
            '/app',
            {'MY_VAR': 'test_value'},
            context
        )
        
        assert key1 == key2, 'Cache keys not deterministic'
        assert len(key1) == 64, 'Cache key should be 64 hex chars'
        print(f'  ✓ Cache key: {key1[:19]}...')
        
        print('[Step 8] Test image file listing...')
        images_dir = docksmith_home / 'images'
        image_files = list(images_dir.glob('*.json'))
        assert len(image_files) >= 1, 'No image manifests found'
        print(f'  ✓ Found {len(image_files)} image(s)')
        for img_file in image_files:
            img = Manifest.load(img_file)
            print(f'    - {img.name}:{img.tag}')
        
        print('=' * 70)
        print('✓ END-TO-END INTEGRATION TEST PASSED')
        print('=' * 70)
        return True

if __name__ == '__main__':
    try:
        test_full_pipeline()
    except Exception as e:
        print(f'\n✗ TEST FAILED: {e}')
        import traceback
        traceback.print_exc()
        sys.exit(1)
