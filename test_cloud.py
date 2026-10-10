"""Offline export/guard tests using synthetic Docker outputs; not image compilation."""
from pathlib import Path
import gzip
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent


class CloudBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        for name in ('build-cloud.sh', 'INSTALL.md'):
            shutil.copy(HERE / name, self.root / name)
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.calls = self.root / 'calls.txt'
        fake = self.bin / 'docker'
        fake.write_text(f'#!{sys.executable}\n' + '''
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
with open(os.environ['FAKE_CALLS'], 'a') as f: f.write(repr(a) + '\\n')
if a[0] == 'build':
    sys.exit(int(os.environ.get('FAKE_BUILD_EXIT', '0')))
elif a[:2] == ['image', 'inspect']:
    is_base = '@sha256:' in a[2]
    config = dict(Env=['SAFE_SYNTHETIC_CONFIG=1'], User='10001', Cmd=['codexmanager-service'],
                  Entrypoint=None, WorkingDir='/app', Healthcheck={'Test':['CMD-SHELL','wget /health']},
                  ExposedPorts={'48760/tcp':{}}, Volumes={'/data':{}})
    if not is_base and os.environ.get('FAKE_CONFIG_MISMATCH'):
        config['Env'] = ['PRIVATE_SENTINEL_NOT_FOR_LOGS=synthetic']
    print(json.dumps([dict(Id='sha256:base' if is_base else 'sha256:patched', Os='linux',
        Architecture='amd64' if is_base else os.environ.get('FAKE_ARCH','amd64'), Config=config)]))
elif a[0] == 'run':
    print('synthetic-isolated-container')
elif a[0] == 'exec':
    sys.exit(int(os.environ.get('FAKE_HEALTH_EXIT', '0')))
elif a[:2] == ['image', 'save']:
    print('synthetic-image-archive')
''')
        fake.chmod(0o755)
        sleep = self.bin / 'sleep'
        sleep.write_text('#!/bin/sh\nexit 0\n')
        sleep.chmod(0o755)
        # The bundled test interpreter must also handle the script's python3 calls.
        (self.bin / 'python3').symlink_to(sys.executable)

    def run_build(self, **extra):
        env = dict(os.environ, PATH=str(self.bin) + os.pathsep + os.environ.get('PATH', ''),
                   FAKE_CALLS=str(self.calls), **extra)
        result = subprocess.run(['/bin/bash', str(self.root / 'build-cloud.sh')],
                                env=env, capture_output=True, text=True)
        calls = self.calls.read_text() if self.calls.exists() else ''
        return result, calls

    def test_verified_export_and_no_production_actions(self):
        result, calls = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('BUILD SUCCEEDED', result.stdout)
        self.assertIn("'--platform', 'linux/amd64'", calls)
        self.assertIn("'--network', 'none'", calls)
        self.assertIn("'--tmpfs', '/data:rw,mode=0777'", calls)
        self.assertIn("['rm', '-f', 'synthetic-isolated-container']", calls)
        self.assertNotIn('/opt/codexmanager-data', calls)
        self.assertNotIn("['stop'", calls)
        self.assertNotIn("['compose'", calls)
        self.assertNotIn("['inspect'", calls)  # No production container inspection.
        metadata = json.loads((self.root/'dist/BUILD-METADATA.json').read_text())
        self.assertTrue(metadata['runtime_config_matches_base'])
        self.assertTrue(metadata['isolated_startup'].startswith('passed'))
        self.assertEqual(metadata['live_upstream_compaction'], 'not tested')
        archive = next((self.root/'dist').glob('*.tar.gz'))
        self.assertEqual(gzip.decompress(archive.read_bytes()), b'synthetic-image-archive\n')
        self.assertTrue((self.root/'dist/SHA256SUMS').exists())

    def test_compile_failure_never_exports(self):
        result, calls = self.run_build(FAKE_BUILD_EXIT='9')
        self.assertEqual(result.returncode, 9)
        self.assertNotIn("['run'", calls)
        self.assertNotIn("['image', 'save'", calls)

    def test_runtime_mismatch_is_refused_without_logging_values(self):
        result, calls = self.run_build(FAKE_CONFIG_MISMATCH='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('PRIVATE_SENTINEL', result.stdout + result.stderr)
        self.assertNotIn("['run'", calls)
        self.assertNotIn("['image', 'save'", calls)

    def test_wrong_architecture_is_refused(self):
        result, calls = self.run_build(FAKE_ARCH='arm64')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("['run'", calls)
        self.assertNotIn("['image', 'save'", calls)

    def test_startup_failure_cleans_only_test_container_and_never_exports(self):
        result, calls = self.run_build(FAKE_HEALTH_EXIT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('BUILD SUCCEEDED', result.stdout)
        self.assertNotIn("['image', 'save'", calls)
        self.assertIn("['rm', '-f', 'synthetic-isolated-container']", calls)
        self.assertNotIn("['stop'", calls)

    def test_existing_results_are_not_overwritten(self):
        (self.root/'dist').mkdir()
        sentinel = self.root/'dist/existing.txt'
        sentinel.write_text('keep existing build results')
        result, calls = self.run_build()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sentinel.read_text(), 'keep existing build results')
        self.assertEqual(calls, '')


if __name__ == '__main__':
    unittest.main(verbosity=2)
