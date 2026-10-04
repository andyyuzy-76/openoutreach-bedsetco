# 可选：原部署专用邮件适配器

**普通安装使用工作台的 SMTP 设置，不需要本目录脚本、SSH 密钥、BedSetCo 服务器或专用包装器。** 默认接入由根目录 `mail_transport.py` 完成。

本目录保留原部署的受限发信与只读审计源码，供维护该部署、恢复原记录。它不是通用 SMTP 安装步骤，新克隆启动时不会自动运行。

## 启用条件

只有实例的私有邮件设置明确选择 `driver=restricted` 且指定 `legacy_ops_dir`，工作台才调用原客户端。页面仅向这种已有实例显示适配器选项；新用户默认 SMTP。

旧实例可以切换 SMTP，原共享历史继续用于去重及提交次数，历史邮件的适配器快照用于查询原 Message-ID。新邮件通过当前 SMTP 发送。

## 保留文件

| 文件 | 用途 |
| --- | --- |
| `importyeti-mail-send.ps1` | 原通道单封受限提交 |
| `importyeti-mail-audit.ps1` | 原通道只读健康、历史、投递及邮箱查询 |
| `importyeti-mail-preflight.ps1` | 原部署网络、服务、包装器及队列预检 |
| `bedsetco-codex-mail-audit` | 原服务器只读接口源码 |
| `deploy-bedsetco-mail-audit.sh` | 原服务器审计部署辅助 |
| `restore-bedsetco-sales-sender.py` | 原服务器包装器受控恢复 |
| `complete-bedsetco-sales-sender.ps1` | 原发件迁移验收，包含显式内部测试 |
| `importyeti-mail-session-preflight.ps1` | 原浏览器接入的可选诊断 |

脚本中的服务器、邮箱、路径与摘要反映原部署，不作为新用户默认设置。工作台仅在明确启用旧适配器时调用前两个脚本，不在启动时执行迁移或测试。

## 恢复原实例

恢复原实例需要其 PowerShell 7、OpenSSH、已核验主机记录、发送／审计密钥及既有服务器端强制命令和包装器。私有密钥不在仓库中，默认位置为当前 Windows 用户的 `.ssh/`。

须保留完整的 `importyeti-gmail-suppression-ledger.json`、`importyeti-submission-ledger.jsonl` 和历史审计。`examples/` 只说明格式，不能替代完整历史；缺少历史会暂停该旧实例导入与发送。

这些恢复条件只适用于专用适配器实例。新用户填写 SMTP 即可使用，无需取得或安装 `/usr/local/libexec/bedsetco-codex-mailer-send`。
