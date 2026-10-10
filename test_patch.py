"""Offline patch/build-script tests. NOT Rust or live gateway verification."""
from pathlib import Path
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('patcher', HERE / 'apply_patch.py')
patcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patcher)


class SourcePatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.gateway = self.root / 'crates/service/src/gateway'
        (self.gateway / 'upstream/attempt_flow').mkdir(parents=True)
        (self.gateway / 'request').mkdir()
        (self.root / 'crates/core/migrations').mkdir(parents=True)
        (self.root / 'Cargo.toml').write_text('[workspace.package]\nversion = "0.6.3"\n')
        self.module = self.gateway / 'mod.rs'
        self.transport = self.gateway / 'upstream/attempt_flow/transport.rs'
        self.module.write_text(patcher.MODULE_ANCHOR + '\n')
        self.transport.write_text('fn synthetic_transport_fixture() {\n' + patcher.TRANSPORT_ANCHOR +
                                  '\n    let body_for_request = encode_request_body(\n}\n')
        (self.root / 'crates/core/migrations/unchanged.sql').write_text('synthetic schema sentinel')
        (self.root / 'docker-compose.yaml').write_text('synthetic config sentinel')

    def snapshot(self):
        return {str(p.relative_to(self.root)): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}

    def apply(self):
        return patcher.patch(self.root, HERE / 'web_search_history.rs')

    def test_success_changes_only_two_gateway_files_and_adds_helper(self):
        before = self.snapshot(); changed = self.apply(); after = self.snapshot()
        self.assertEqual(len(changed), 3)
        self.assertEqual({p for p in before if before[p] != after[p]},
                         {'crates/service/src/gateway/mod.rs',
                          'crates/service/src/gateway/upstream/attempt_flow/transport.rs'})
        self.assertEqual(set(after) - set(before), {'crates/service/src/gateway/request/web_search_history.rs'})
        text = self.transport.read_text()
        self.assertLess(text.index('rewrite_http_body('), text.index('let body_for_request = encode_request_body('))
        self.assertIn('incoming_headers.passthrough_codex_headers()', text)
        self.assertIn('is_codex_backend(target_url)', text)

    def test_wrong_version_is_rejected_without_writes(self):
        for version in ['0.6.0', '0.6.2', '0.6.4']:
            (self.root / 'Cargo.toml').write_text(f'[workspace.package]\nversion = "{version}"\n')
            before = self.snapshot()
            with self.assertRaises(ValueError): self.apply()
            self.assertEqual(self.snapshot(), before)

    def test_crlf_line_endings_are_preserved(self):
        for path in [self.module, self.transport]:
            path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
        self.apply()
        for path in [self.module, self.transport]:
            data = path.read_bytes()
            self.assertNotIn(b'\n', data.replace(b'\r\n', b''))

    def test_duplicate_or_missing_hooks_are_rejected_without_writes(self):
        original = self.transport.read_text()
        for replacement in ['', patcher.TRANSPORT_ANCHOR + '\n' + patcher.TRANSPORT_ANCHOR]:
            self.transport.write_text(original.replace(patcher.TRANSPORT_ANCHOR, replacement))
            before = self.snapshot()
            with self.assertRaises(ValueError): self.apply()
            self.assertEqual(self.snapshot(), before)

    def test_module_conflict_and_repeat_application_do_not_write(self):
        self.apply(); before = self.snapshot()
        with self.assertRaises(ValueError): self.apply()
        self.assertEqual(self.snapshot(), before)

    def test_changed_compression_layout_is_rejected_without_writes(self):
        self.transport.write_text(self.transport.read_text().replace('encode_request_body(', 'different_encoder('))
        before = self.snapshot()
        with self.assertRaises(ValueError): self.apply()
        self.assertEqual(self.snapshot(), before)

    def test_hook_after_compression_is_rejected_without_writes(self):
        self.transport.write_text('let body_for_request = encode_request_body(\n' + patcher.TRANSPORT_ANCHOR)
        before = self.snapshot()
        with self.assertRaises(ValueError): self.apply()
        self.assertEqual(self.snapshot(), before)

    def test_missing_request_directory_is_rejected_without_writes(self):
        (self.gateway / 'request').rmdir()
        before = self.snapshot()
        with self.assertRaises(ValueError): self.apply()
        self.assertEqual(self.snapshot(), before)


class BuildScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.log = self.root / 'calls.txt'
        shutil.copy(HERE / 'build.sh', self.root / 'build.sh')
        self.bindir = self.root / 'bin'; self.bindir.mkdir()
        fake = self.bindir / 'docker'
        fake.write_text(f'#!{sys.executable}\n' + '''import os,sys
from pathlib import Path
a=sys.argv[1:]
with open(os.environ['FAKE_DOCKER_LOG'],'a') as f: f.write(repr(a)+'\\n')
if a[:1]==['inspect']:
 print('sha256:synthetic-current' if a[2]=='{{.Image}}' else 'ghcr.io/qxcnm/codexmanager-service:latest')
elif a[:2]==['image','inspect']:
 if '.RepoDigests' in a[3]: print(os.environ.get('FAKE_DIGEST','ghcr.io/qxcnm/codexmanager-service@sha256:synthetic-digest'))
 elif '.Architecture' in a[3]: print('linux/amd64')
 else:
  value=os.environ.get('FAKE_EXISTING','')
  if not value: sys.exit(1)
  print(value)
elif a[:1]==['build']:
 sys.exit(int(os.environ.get('FAKE_BUILD_EXIT','0')))
elif a[:1]==['run']:
 print('synthetic-smoke-container')
elif a[:1]==['exec']:
 sys.exit(int(os.environ.get('FAKE_HEALTH_EXIT','0')))
''')
        fake.chmod(0o755)
        sleep = self.bindir / 'sleep'; sleep.write_text('#!/bin/sh\nexit 0\n'); sleep.chmod(0o755)

    def run_build(self, **extra):
        env = dict(os.environ, PATH=str(self.bindir) + os.pathsep + os.environ.get('PATH', ''),
                   FAKE_DOCKER_LOG=str(self.log), **extra)
        result = subprocess.run(['/bin/sh', str(self.root / 'build.sh')], env=env,
                                capture_output=True, text=True)
        calls = self.log.read_text() if self.log.exists() else ''
        return result, calls

    def test_immutable_runtime_and_rollback_tag_no_deployment_actions(self):
        result, calls = self.run_build()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('UPSTREAM_RUNTIME=ghcr.io/qxcnm/codexmanager-service@sha256:synthetic-digest', calls)
        self.assertIn("'--platform', 'linux/amd64'", calls)
        self.assertIn('0.6.3-original', calls)
        self.assertNotIn("['stop'", calls); self.assertNotIn("['compose'", calls)
        self.assertNotIn("['pull'", calls)
        self.assertIn("'--network', 'none'", calls)
        self.assertIn("'--tmpfs', '/data:rw,mode=0777'", calls)
        self.assertIn("['rm', '-f', 'synthetic-smoke-container']", calls)
        self.assertNotIn('/opt/codexmanager-data', calls)
        self.assertEqual(len(list(self.root.glob('baseline-*.txt'))), 1)

    def test_local_runtime_fallback(self):
        result, calls = self.run_build(FAKE_DIGEST='')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('UPSTREAM_RUNTIME=codexmanager-service:0.6.3-original', calls)

    def test_conflicting_rollback_refuses_without_retag_or_build(self):
        result, calls = self.run_build(FAKE_EXISTING='sha256:different-baseline')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn("['build'", calls); self.assertNotIn("['image', 'tag'", calls)

    def test_build_failure_is_not_reported_as_success(self):
        result, _ = self.run_build(FAKE_BUILD_EXIT='9')
        self.assertEqual(result.returncode, 9)
        self.assertNotIn('构建成功', result.stdout)

    def test_smoke_failure_refuses_deployment_and_cleans_only_its_container(self):
        result, calls = self.run_build(FAKE_HEALTH_EXIT='1')
        self.assertEqual(result.returncode, 1)
        self.assertNotIn('构建成功', result.stdout)
        self.assertIn("['rm', '-f', 'synthetic-smoke-container']", calls)
        self.assertNotIn("['stop'", calls)


if __name__ == '__main__':
    unittest.main(verbosity=2)
