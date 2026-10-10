# CodexManager 0.6.3 compact-history hotfix: cloud build

Independent community compatibility patch; not an official CodexManager or OpenAI release.

- Target: **linux/amd64**, port **48760**.
- Original runtime: `ghcr.io/qxcnm/codexmanager-service@sha256:3bf5b865abe76ca2da828d82ecd5d31c20f497b9f9c67449790b5bf60270d26f`.
- Official `v0.6.3` source commit: `764531421428e35b0fbb3aab8f4f777887e7a44a`.
- Final runtime inherits the original ENV, USER, CMD, entrypoint, healthcheck, volumes and ports. Only the service executable is replaced; patch metadata and upstream license are added.
- No server credentials, persistent data, RPC tokens or conversation history are present in this repository or needed for the build.

GitHub Actions tests the source patch, compiles the whole service test suite, builds the release binary, compares inherited runtime configuration, and checks startup in an isolated container. Only after those checks pass is a Docker image archive uploaded as an artifact. A successful build does **not** prove live upstream compaction is fixed; that must be verified with a new test conversation after deployment.

The compatibility change supplies a missing search declaration when replaying native `web_search_call` history to the ChatGPT Codex backend. It preserves history and response streaming; it does not fabricate completion events. Standard and Lite HTTP Responses replay are covered. Standalone `/responses/compact`, external vendors, direct WebSocket ingress and dedicated aggregate API forwarding are not modified.

Sources: [upstream v0.6.3](https://github.com/qxcnm/Codex-Manager/tree/v0.6.3), [same-symptom reproduction](https://github.com/Wei-Shaw/sub2api/pull/7939).

`build.sh` is the original server-oriented build wrapper retained only for its offline regression tests. **Do not run it on a low-memory production server.** Cloud Actions run `build-cloud.sh`; it does not inspect or access production containers.

See [INSTALL.md](INSTALL.md) for image import and 1Panel deployment. No container registry publishing, deployment keys, paid runners, schedules or server connection are configured.
