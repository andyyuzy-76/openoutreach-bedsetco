# 邮件服务器接入

工作台调用本目录的受限邮件客户端脚本，并与每日任务共用历史台账。

## 现有服务器

发件人为 `sales@bedsetco.com`。客户端连接既有 BedSetCo 邮件服务器；发送和审计使用不同 SSH 密钥及受限命令。

| 文件 | 作用 |
| --- | --- |
| `importyeti-mail-send.ps1` | 校验单个发件人、单个收件人和 Message-ID，向服务器提交一封邮件 |
| `importyeti-mail-audit.ps1` | 调用只读审计接口，检查健康状态、历史提交、投递日志和允许的邮箱查询 |
| `importyeti-mail-preflight.ps1` | 检查网络、邮件服务、发送包装器版本及队列状态 |
| `bedsetco-codex-mail-audit` | 服务器端只读审计接口源码 |
| `deploy-bedsetco-mail-audit.sh` | 既有服务器部署只读审计程序的辅助脚本 |
| `restore-bedsetco-sales-sender.py` | 对已验证的服务器发送包装器做受控发件人恢复 |
| `complete-bedsetco-sales-sender.ps1` | 核验已有发件人迁移流程，包含受控内部测试提交 |
| `importyeti-mail-session-preflight.ps1` | 兼容原浏览器接入的可选诊断 |

日常工作台使用前两个客户端脚本。迁移辅助脚本仅用于相应迁移流程，启动工作台不会执行它们。

## 本机私有状态

默认密钥位置为：

- `%USERPROFILE%\.ssh\bedsetco_mailer_ed25519`
- `%USERPROFILE%\.ssh\bedsetco_mail_audit_ed25519`

脚本使用严格的 SSH 主机校验。需要通过私有渠道恢复密钥和已确认的主机记录，并确保 `ssh.exe`、`pwsh` 可用。

本目录以下文件由现有部署维护，不进入 Git：

- `importyeti-gmail-suppression-ledger.json`
- `importyeti-submission-ledger.jsonl`
- 历史审计、客户表和邮件导出

历史名单的字段格式见 `examples/suppression-ledger.example.json`。实际部署应恢复完整原始记录。

## 服务器端依赖

发送客户端使用邮件服务器上已经安装的 `/usr/local/libexec/bedsetco-codex-mailer-send`。该包装器和 SSH 强制命令配置属于既有邮件服务器部署；迁移到其他服务器时还需要通过私有渠道取得并安装相应服务器配置和发送包装器。

审计程序应作为 SSH 强制命令运行，它仅提供有限只读操作。脚本中的既有服务路径、服务器地址和已验证包装器摘要反映当前 BedSetCo 部署；更换服务器时需同步核对客户端参数和服务器配置。

克隆源码不会自动建立服务器账户、密钥或发信权限。
