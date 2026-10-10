#!/bin/sh
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
command -v docker >/dev/null 2>&1 || { echo '需要在有 Docker 的服务器运行本脚本。' >&2; exit 1; }

container=codexmanager-service
rollback=codexmanager-service:0.6.3-original
patched=codexmanager-service:0.6.3-compact-fix
image_id=$(docker inspect --format '{{.Image}}' "$container")
image_name=$(docker inspect --format '{{.Config.Image}}' "$container")
repo_digest=$(docker image inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{end}}' "$image_id")
platform=$(docker image inspect --format '{{.Os}}/{{.Architecture}}{{if .Variant}}/{{.Variant}}{{end}}' "$image_id")
existing=$(docker image inspect --format '{{.Id}}' "$rollback" 2>/dev/null || true)
if [ -n "$existing" ] && [ "$existing" != "$image_id" ]; then
    echo "回退标签 $rollback 已指向其他镜像，停止构建，避免覆盖回退依据。" >&2
    exit 1
fi
docker image tag "$image_id" "$rollback"
runtime=${repo_digest:-$rollback}
record="baseline-$(date +%Y%m%d-%H%M%S).txt"
umask 077
{
    printf 'original_image=%s\noriginal_image_id=%s\noriginal_repo_digest=%s\n' "$image_name" "$image_id" "$repo_digest"
    printf 'rollback_image=%s\npatched_image=%s\n' "$rollback" "$patched"
    printf 'platform=%s\n' "$platform"
} > "$record"
printf '已保留原镜像：%s\n构建记录：%s\n' "$rollback" "$record"
printf '仅构建新镜像，不停止或更新现有容器，不访问持久化目录。\n'
docker build --platform "$platform" --build-arg "UPSTREAM_RUNTIME=$runtime" --build-arg "BUILD_JOBS=${BUILD_JOBS:-1}" -t "$patched" .

# Verify the replacement binary runs in the inherited runtime. No host ports,
# network access, real credentials, or persistent data mounts are supplied.
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
    --env CODEXMANAGER_RPC_TOKEN=isolated-startup-test-only \
    "$patched")
attempt=0
until docker exec "$smoke_id" wget --quiet --tries=1 --timeout=2 --spider http://127.0.0.1:48760/health >/dev/null 2>&1; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 45 ]; then
        echo '新镜像隔离启动检查失败，不能部署；原有容器未修改。' >&2
        docker logs --tail 40 "$smoke_id" >&2 || true
        exit 1
    fi
    sleep 1
done
cleanup
smoke_id=''
printf '\n构建成功：%s\n' "$patched"
printf '下一步：备份数据，然后在原 1Panel 编排中仅替换后端 image 并重建该容器。\n'
printf '端口、环境变量、Web 镜像和数据挂载不需要修改。\n'
