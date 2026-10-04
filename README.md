# BedSetCo 客户开发工作台

基于 [OpenOutreach](https://github.com/eracle/OpenOutreach)、[OpenOutFind](https://github.com/eracle/OpenOutFind) 和 [OpenOutSend](https://github.com/eracle/OpenOutSend) 改造的本地客户开发系统。

用于 BedSetCo 床品业务：从公开公司网站发现潜在客户，核对公开联系方式，生成首封开发信，通过自有邮件服务器发送并核查投递。

## 当前功能

- 免费搜索公司官网和公开邮箱；客户采集不依赖 BetterContact 等付费数据服务。
- 支持客户 CSV 导入、草稿编辑、审核、停用和公司域名去重。
- 保留兼容 OpenAI 接口的 NewAPI 配置入口。API 密钥使用 Windows DPAPI 保存在本机。
- 实际发件人和回复地址为 `sales@bedsetco.com`。
- 通过既有受限 SSH 发送通道连接 BedSetCo 邮件服务器；发信前刷新历史记录，提交前持久化 Message-ID。
- 工作台和每日任务共用发信台账，每日客户提交上限为 3 封；结果不明时进入核查状态。
- 按 Message-ID 核查对方邮件服务器的接收结果。对方服务器接收不等于进入收件箱或已阅读。
- 日常流程由现有 Codex 每日任务执行：北京时间 09:30 免费搜集客户，业务与邮箱核对后，默认发送 1 封合格新客户首信并核查结果。

现有部署于 2026-10-04 完成实际外发，已通过邮件日志核实对方 MX 的 `250` 接收结果。详细邮件和客户记录保存在本地。

## 安装与启动

运行环境：Windows、Python 3.12、uv、PowerShell 7、OpenSSH 客户端。

在仓库根目录运行：

```powershell
uv sync --frozen --python 3.12
.\.venv\Scripts\python.exe .\bedsetco.py
```

也可以双击 `启动工作台.cmd`。默认地址为 <http://127.0.0.1:8766/>。

需要后台启动时：

```powershell
.\.venv\Scripts\python.exe .\bedsetco.py --no-browser
```

`关闭工作台.cmd` 会核对进程属于本项目后关闭它。

## 恢复私有配置

新克隆只有源码。原机器的以下状态需要通过私有渠道恢复：

1. `data/`：客户队列、DPAPI 配置、采集结果、发信与投递核查记录。
2. `ops/importyeti-gmail-suppression-ledger.json`：历史客户及停用名单。
3. `ops/importyeti-submission-ledger.jsonl`：每日任务和工作台共用的提交台账。
4. 本机用户 `.ssh/` 中的发送密钥、只读审计密钥，以及已确认的服务器主机记录。

DPAPI 配置依赖原 Windows 用户和机器，换机器后需要在页面重新录入 NewAPI 密钥。

缺少历史去重台账时，系统会暂停导入和发送。`examples/` 中的文件仅说明字段格式；已有发信历史的部署应恢复完整历史，不能用空模板替代。

邮件接入和服务器端依赖见 [ops/README.md](ops/README.md)。

## 每日自动化

每天的实际外发由原 Codex 每日任务协调。任务配置属于本机 Codex，不会随 Git 克隆自动安装。换机器时应重新配置任务并恢复共用台账。

仓库内的采集入口：

```powershell
.\.venv\Scripts\python.exe .\daily_collect.py --count 5
```

这一步会免费采集最多 5 家公司，并把候选导入草稿队列。实际自动发送还需要每日任务完成业务核对、官网邮箱用途核对、历史去重、草稿确认和投递核查。调度说明见 [使用说明.md](使用说明.md)。

## 项目结构

| 路径 | 用途 |
| --- | --- |
| `bedsetco.py` | 本地工作台、客户队列、审核、受限发送及投递核查 |
| `free_finder.py` | 免费官网搜索及公开邮箱采集 |
| `finder.py` | 采集配置、NewAPI 设置、DPAPI 存储及后台采集 |
| `daily_collect.py` | 每日任务的同步采集入口 |
| `product.md`、`target.md` | 产品事实约束和可调整的目标客户画像 |
| `ops/` | 邮件发送、只读审计、预检和迁移辅助源码 |
| `src/` | 三个上游项目的源码快照，包含本地改造 |
| `data/` | 本地运行时数据，由程序生成并由 Git 忽略 |

本仓库把原来分散在项目与共享 `ops` 目录的源码整理到一起。仓库版工作台使用根目录内的 `ops/`；正在运行的原部署继续使用原有目录布局。

## 许可证与上游

保留上游 GPLv3 许可证及相应声明。根目录 [LICENSE](LICENSE) 是 GPLv3 正文；各上游目录保留原许可证。来源、基准提交和修改记录见 [源码版本.md](源码版本.md) 与 [NOTICE.md](NOTICE.md)。

本仓库保留源码快照及已修改内容，上游 Git 历史可从对应公开仓库获取。
