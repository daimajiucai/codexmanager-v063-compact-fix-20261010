# 独立兼容性补丁：CodexManager 0.6.3 / linux/amd64

这是社区兼容性补丁，不是官方发布。镜像复用原版 0.6.3 的不可变运行环境，只替换后端程序，保持端口 **48760** 和原有运行配置。无需在低内存服务器上编译；下载五个附件、核对 SHA256，再用 `docker load` 导入。

构建来源：[已成功的固定构建](https://github.com/daimajiucai/codexmanager-v063-compact-fix-20261010/actions/runs/38037757233)。完整未过滤 Rust 库测试已在未修改原版和补丁版各运行一次：4 项上游模型目录数量断言在前后同样失败，8 项上游测试原本忽略；没有新增失败，新增 13 项补丁测试全部通过。其他服务测试目标和文档测试、运行配置比较以及无网络、无生产数据的隔离启动检查通过。准确结果见 `TEST-RESULTS.json` 和 `BUILD-METADATA.json`。

Release 发布任务再次验证文件校验和并实际导入 Docker 镜像，核对 image ID 和平台后才上传附件。没有账号、Token、服务器数据或会话内容。

**重要：构建检查不能代替真实上游验收。** 补丁只覆盖 HTTP Responses 请求中原生网页搜索历史缺少工具声明的兼容问题，不修改独立 compact、其他供应商、专用聚合或直接 WebSocket 入口。切换后需在自建中转的新测试会话中执行“普通消息 → 网页搜索 → 压缩”。

切换前备份持久化数据并保留原版镜像。在**原 1Panel 编排**中只替换后端的 `image` 为 `codexmanager-service:0.6.3-compact-fix`，设置 `pull_policy: never` 并重建后端。Web 镜像、端口、环境变量和 `/opt/codexmanager-data:/data` 不变；详细步骤和回退方法见 `INSTALL.md`。不要删除数据目录或执行 `down -v`。
