#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
umask 077

platform=linux/amd64
runtime=ghcr.io/qxcnm/codexmanager-service@sha256:3bf5b865abe76ca2da828d82ecd5d31c20f497b9f9c67449790b5bf60270d26f
source_commit=764531421428e35b0fbb3aab8f4f777887e7a44a
patched=codexmanager-service:0.6.3-compact-fix
archive=codexmanager-service-0.6.3-compact-fix-linux-amd64.tar.gz

command -v docker >/dev/null
command -v python3 >/dev/null
if [ -e dist ]; then
    echo 'dist already exists; use a fresh build directory to preserve previous results.' >&2
    exit 1
fi
mkdir dist
docker pull --platform "$platform" "$runtime"
docker build --platform "$platform" --progress plain \
    --build-arg "UPSTREAM_RUNTIME=$runtime" \
    --build-arg "UPSTREAM_SOURCE_COMMIT=$source_commit" \
    --build-arg "BUILD_JOBS=${BUILD_JOBS:-2}" -t "$patched" .

# Inspect in memory; do not publish environment values or credentials.
python3 - "$runtime" "$patched" "$source_commit" <<'PY'
import json, subprocess, sys
base_name, patched_name, commit = sys.argv[1:]
base, patched = [json.loads(subprocess.check_output(
    ['docker', 'image', 'inspect', name], text=True))[0] for name in (base_name, patched_name)]
for key in ('Env', 'User', 'Cmd', 'Entrypoint', 'WorkingDir', 'Healthcheck',
            'ExposedPorts', 'Volumes', 'StopSignal'):
    if base['Config'].get(key) != patched['Config'].get(key):
        raise SystemExit('Refusing changed runtime configuration: ' + key)
if (patched['Os'], patched['Architecture']) != ('linux', 'amd64'):
    raise SystemExit('Refusing incompatible image architecture')
report_text = subprocess.check_output(['docker', 'run', '--rm', '--platform', 'linux/amd64',
    '--network', 'none', '--entrypoint', 'cat', patched_name,
    '/usr/local/share/codexmanager-hotfix/TEST-RESULTS.json'], text=True)
test_report=json.loads(report_text)
if test_report.get('patch_tests_passed') != 13 or not test_report.get('verification', '').startswith('passed;'):
    raise SystemExit('Refusing unverified Rust test report')
with open('dist/TEST-RESULTS.json', 'w') as f: f.write(report_text)
metadata = dict(base_image=base_name, base_image_id=base['Id'], image=patched_name,
                image_id=patched['Id'], platform='linux/amd64', upstream_version='0.6.3',
                upstream_source_commit=commit, runtime_config_matches_base=True,
                rust_tests=test_report, isolated_startup='pending',
                live_upstream_compaction='not tested')
with open('dist/BUILD-METADATA.json', 'w') as f:
    json.dump(metadata, f, indent=2); f.write('\n')
print('Inherited runtime configuration and linux/amd64 architecture verified.')
PY

smoke_id=''
cleanup() {
    if [ -n "$smoke_id" ]; then docker rm -f "$smoke_id" >/dev/null 2>&1 || true; fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
smoke_id=$(docker run --detach --platform "$platform" --network none \
    --tmpfs /data:rw,mode=0777 \
    --env CODEXMANAGER_SERVICE_ADDR=127.0.0.1:48760 \
    --env CODEXMANAGER_DB_PATH=/data/codexmanager.db \
    --env CODEXMANAGER_RPC_TOKEN=isolated-startup-test-only "$patched")
for attempt in $(seq 1 60); do
    if docker exec "$smoke_id" wget --quiet --tries=1 --timeout=2 --spider \
        http://127.0.0.1:48760/health >/dev/null 2>&1; then
        break
    fi
    if [ "$attempt" -eq 60 ]; then
        echo 'Isolated startup failed; no deployable archive will be exported.' >&2
        docker logs --tail 40 "$smoke_id" >&2 || true
        exit 1
    fi
    sleep 2
done
cleanup
smoke_id=''
python3 - <<'PY'
import json
p='dist/BUILD-METADATA.json'
with open(p) as f: metadata=json.load(f)
metadata['isolated_startup']='passed; isolated tmpfs, no network, no published ports'
with open(p,'w') as f: json.dump(metadata,f,indent=2); f.write('\n')
PY
docker image save "$patched" | gzip -1 > "dist/$archive"
cp INSTALL.md dist/INSTALL.md
(cd dist && sha256sum "$archive" BUILD-METADATA.json INSTALL.md TEST-RESULTS.json > SHA256SUMS)
printf '\nBUILD SUCCEEDED: %s\nArchive: dist/%s\n' "$patched" "$archive"
