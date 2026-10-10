# 镜像导入和 1Panel 切换

这是同版本 0.6.3 的独立兼容性补丁，不是官方修补版。先确保 Actions 完整成功，再从构建结果下载镜像 artifact，解压到一个独立目录并上传服务器。不要上传或公开账号数据、Token 或备份。

## 1. 核对完整性并导入

在存放四个导出文件的服务器目录执行：

```sh
sha256sum -c SHA256SUMS
# 必须全部显示 OK 后才执行下一条。
docker load -i codexmanager-service-0.6.3-compact-fix-linux-amd64.tar.gz
docker image inspect codexmanager-service:0.6.3-compact-fix --format '{{.Id}} {{.Os}}/{{.Architecture}}'
```

输出的 image ID 应与 BUILD-METADATA.json 一致，平台必须为 linux/amd64。导入本身不会停止或替换正在运行的容器。

## 2. 保留原镜像并备份数据

切换前，用 `docker inspect --format '{{.Image}}' codexmanager-service` 确认当前原版的 image ID 与 BUILD-METADATA.json 中的 base_image_id 一致。若不一致，暂停切换，不要猜测版本兼容性。

如果之前的构建脚本已创建 `codexmanager-service:0.6.3-original`，保留它。若尚未创建，可将确认匹配的原版 image ID 标记为该回退标签；不要覆盖指向其他 image ID 的已有回退标签。

持久化不是备份。建议在维护窗口暂停 Web 和后端容器，备份整个 `/opt/codexmanager-data` 目录，然后恢复原服务；不要删除该目录，不要执行 `down -v`，不要公开备份。

## 3. 编辑原 1Panel 编排

只替换后端镜像，并禁止强制拉取本地标签：

```yaml
image: codexmanager-service:0.6.3-compact-fix
pull_policy: never
```

Web 必须保留原 Web 镜像，不要换成后端镜像，不要顺带升级 Web。为避免重新拉取 `latest`，Web 也可设置 `pull_policy: never`，前提是原 Web 镜像仍在本地。

端口 `48760`、`network_mode: host`、`restart: always`、所有环境变量以及 `/opt/codexmanager-data:/data` 全部保持原样。重建后端容器，不是只点击“重启”。不需要另建编排，不需要新增永久容器或调整反向代理。

## 4. 验收与回退

```sh
curl -fsS http://127.0.0.1:48760/health
docker inspect --format '{{.Config.Image}} {{.Image}}' codexmanager-service
```

确认 Web 正常，再在自建中转的新测试会话中执行：普通消息 → 网页搜索 → 压缩。以实际压缩成功为修复验收；健康检查和构建通过不能替代真实上游验证。

回退时将后端镜像改回确认匹配的 `codexmanager-service:0.6.3-original` 并重建，保留端口、配置和数据挂载。
