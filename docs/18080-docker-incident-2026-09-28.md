# 18080 无响应排查（2026-09-28）

本次为现场诊断，未重启 Docker、未修改项目代码或配置、未删除运行数据。

## 已确认

- 18080 首页与 `/api/health` 均能建立 TCP 连接，但 8 秒内无 HTTP 响应。
- Docker Unix socket 的 `/_ping` 5 秒超时，`docker compose ps` 也持续等待。故障位于 Docker 层。
- 10:20:50（北京时间）虚拟机启动；本会话曾验证项目首页 HTTP 200、API healthy。
- 10:27:55.752 虚拟机日志出现：`FATAL: running services: running time: backend time: Get "http://ipc/time": context deadline exceeded`。
- 紧接着 `preparing to power the VM off...`，关闭 dockerd/containerd 等进程；10:27:59.706 宿主日志记录 `VM has stopped gracefully`。当前虚拟机进程不存在，Docker backend 进程仍存活但 API 无响应。
- 直接故障链：宿主 backend 内部时间接口超时 → VM 的 init 视为致命服务错误并关机 → 容器服务不可用，而端口代理仍接受连接，浏览器持续等待。
- 本次清理范围只含历史研究产物；正式代码、素材及交付目录共 1450 个文件清理前后 SHA256 一致，未触及 Docker 数据卷。VM 停止时间早于清理执行记录。

- 历史 `console.log.0` 在 9 月 26 日 06:15:53（北京时间）出现完全相同的内部时间接口超时 FATAL；这是重复发生的 Docker 故障。

## 资源与不确定性

- 排查时数据卷仅余约 2.5 GiB，`df` 使用率显示 100%。
- `vm.swapusage` 显示约 28120 MiB 交换空间已使用；Docker 虚拟机配置约 8092 MiB 内存，宿主总内存 24576 MiB。
- Docker 虚拟磁盘实际占用约 38 GiB；它包含现有运行数据，不应直接删除。
- 上述资源状态值得优先处理，但没有找到能直接证明本次故障由磁盘写满或 OOM 引起的日志。尚不能将资源压力断言为唯一根因。
- backend 进程采样主要呈等待状态，未符号化的 Go 栈不足以确定具体死锁位置。

## 后续处理顺序

1. 先盘点并释放足够磁盘空间，关闭不用的高内存应用；不直接删 Docker 数据卷或虚拟磁盘。
2. 完整恢复 Docker 引擎，再用现有 `.env.marketing` 启动本项目，验证首页、API 和容器健康。
3. 监测超过此次约 7 分钟的复发窗口；若仍出现相同 IPC timeout，再针对 Docker Desktop 版本兼容性和 backend 阻塞进行排查。

## 原始证据

- `~/Library/Containers/com.docker.docker/Data/log/vm/console.log`
- `~/Library/Containers/com.docker.docker/Data/log/vm/init.log`
- `~/Library/Containers/com.docker.docker/Data/log/host/com.docker.virtualization.log`
- `~/Library/Containers/com.docker.docker/Data/log/host/com.docker.backend.log`
- `/tmp/marketing-docker-hang-sample.txt`

## 10:42 用户释放磁盘后恢复

用户清理 npm 缓存后，磁盘可用空间增至约 127 GiB（使用率 69%）。按用户要求重启 Docker；残留 backend 无响应，使用 Docker Desktop 官方 force stop 后重新启动。使用原有 .env.marketing 执行 compose up -d --no-build --wait，项目服务健康。首页 HTTP 200，API status=ok，runtime=deerflow，model_configured=true，两项响应约 20 ms。当前已恢复，尚未完成长期稳定性观察，不能仅凭一次恢复断言根因已消除。
