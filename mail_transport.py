"""User-configured SMTP transport, with an opt-in adapter for existing installations."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import smtplib
import ssl
import subprocess

import private_store

DATA = private_store.DATA
DEFAULT = {'driver': 'smtp', 'revision': 0, 'host': '', 'port': 587,
           'security': 'starttls', 'use_auth': True, 'username': '', 'password': '',
           'sender_email': '', 'reply_to': '', 'display_name': '', 'daily_limit': 3,
           'interval_seconds': 240, 'timeout': 30, 'legacy_ops_dir': ''}
locked = private_store.locked


def email(value):
    value = str(value).strip().lower()
    if len(value) > 254 or not re.fullmatch(r'[a-z0-9.!#$%&\x27*+/=?^_`{|}~-]+@[a-z0-9.-]+\.[a-z]{2,}', value):
        raise ValueError('邮箱须为一个完整邮箱地址，不包含显示名称或换行')
    local, domain = value.rsplit('@', 1)
    if len(local) > 64 or local.startswith('.') or local.endswith('.') or '..' in value:
        raise ValueError('邮箱格式不正确')
    if any(not label or label.startswith('-') or label.endswith('-') or len(label) > 63 for label in domain.split('.')):
        raise ValueError('邮箱域名格式不正确')
    return value


def settings():
    config = dict(DEFAULT)
    config.update(private_store.load('mail', DEFAULT))
    if config['driver'] == 'smtp' and 'OUTREACH_SMTP_PASSWORD' in os.environ:
        config['password'] = os.environ['OUTREACH_SMTP_PASSWORD']
    return config


def sender_email():
    return settings()['sender_email']


def snapshot(config=None):
    config = config or settings()
    # Include credential changes in approvals without writing credentials to the queue.
    public = {k: v for k, v in config.items() if k != 'password'}
    public['credential_version'] = hashlib.sha256(str(config['password']).encode()).hexdigest() if config['password'] else ''
    # The fingerprint is saved, rather than the password hash itself.
    fingerprint = hashlib.sha256(json.dumps(public, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    public.pop('credential_version')
    public['fingerprint'] = fingerprint
    return public


def validate(config):
    config['sender_email'] = email(config['sender_email'])
    config['reply_to'] = email(config.get('reply_to') or config['sender_email'])
    if any(ch in config['display_name'] for ch in '\r\n\x00') or len(config['display_name']) > 200:
        raise ValueError('发件显示名称不能换行或超过 200 字符')
    for key, lower, upper in [('daily_limit', 1, 10000), ('interval_seconds', 0, 86400),
                              ('timeout', 5, 120), ('port', 1, 65535)]:
        if not isinstance(config[key], int) or not lower <= config[key] <= upper:
            raise ValueError('端口、超时、每日上限或发送间隔超出允许范围')
    if config['driver'] == 'restricted':
        if not config.get('legacy_ops_dir'):
            raise ValueError('旧部署适配器缺少私有配置')
        return config
    if config['driver'] != 'smtp':
        raise ValueError('不支持此邮件接入方式')
    host = str(config['host']).strip()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        if not re.fullmatch(r'[a-zA-Z0-9](?:[a-zA-Z0-9.-]{0,251}[a-zA-Z0-9])?', host) or '..' in host:
            raise ValueError('SMTP 服务器只填写主机名或 IP，不包含网址、端口或路径')
    config['host'] = host
    if config['security'] not in ('starttls', 'ssl', 'none'):
        raise ValueError('请选择 STARTTLS、SSL/TLS 或不加密')
    if config['use_auth']:
        if not config['username'] or not config['password']:
            raise ValueError('启用账号认证时需要用户名和密码／授权码')
        if config['security'] == 'none':
            raise ValueError('账号认证需要 STARTTLS 或 SSL/TLS；不加密仅用于无需认证的邮件中继')
    if any(ch in config['username'] for ch in '\r\n\x00') or len(config['username']) > 320:
        raise ValueError('SMTP 用户名格式不正确')
    return config


def require_ready(config=None):
    config = dict(config or settings())
    if not config['sender_email'] or (config['driver'] == 'smtp' and not config['host']):
        raise ValueError('请先在「邮件服务器」中保存你自己的发件邮箱和 SMTP 配置')
    return validate(config)


def save(values):
    with locked('mail'):
        old = settings()
        if str(old['revision']) != values.get('mail_revision'):
            raise ValueError('邮件配置已变更，请刷新页面后保存')
        driver = values.get('driver', 'smtp')
        if driver == 'restricted':
            if old['driver'] != 'restricted':
                raise ValueError('旧接入仅供已配置的部署使用，请填写 SMTP 配置')
            return '已保留当前邮件接入。可选择 SMTP 并填写自己的邮箱配置。'
        config = dict(old)
        config['driver'] = 'smtp'
        for key in ('host', 'security', 'username', 'sender_email', 'reply_to', 'display_name'):
            config[key] = values.get(key, '').strip()
        # Quotas now belong to daily_settings. Keep the legacy values in this
        # snapshot stable, so changing quantity does not change a draft's account.
        for key in ('port', 'timeout'):
            try:
                config[key] = int(values.get(key, str(DEFAULT[key])))
            except ValueError:
                raise ValueError('端口和超时须为整数') from None
        config['use_auth'] = values.get('use_auth') == 'on'
        new_password = values.get('smtp_password', '')
        changed_server = old['driver'] != 'smtp' or any(config[k] != old[k] for k in ('host', 'port', 'security', 'username'))
        if new_password:
            config['password'] = new_password
        elif values.get('clear_password') == 'on' or changed_server:
            config['password'] = ''
        # A process environment password is deliberate and is never copied into the file.
        effective = dict(config)
        if 'OUTREACH_SMTP_PASSWORD' in os.environ:
            effective['password'] = os.environ['OUTREACH_SMTP_PASSWORD']
            config['password'] = ''
        validate(effective)
        config.update({k: effective[k] for k in ('host', 'sender_email', 'reply_to')})
        config['revision'] = old['revision'] + 1
        private_store.save('mail', config)
    return '邮件配置已保存。旧草稿需更新发件配置并重新审核；保存配置不会发送邮件。'


def legacy_ops(config=None):
    value = (config or settings()).get('legacy_ops_dir', '')
    return Path(value) if value else None


def ledger_path():
    ops = legacy_ops()
    if ops:
        if not (ops / 'importyeti-gmail-suppression-ledger.json').exists():
            raise ValueError('已有部署的历史去重台账缺失，请恢复历史记录')
        return ops / 'importyeti-submission-ledger.jsonl'
    private_store.prepare()
    return DATA / 'submission-ledger.jsonl'


def _legacy_call(operation, config, *arguments, timeout=35):
    ops = legacy_ops(config)
    pwsh = shutil.which('pwsh')
    script = ops / ('importyeti-mail-send.ps1' if operation == 'send' else 'importyeti-mail-audit.ps1')
    if not pwsh or not script.is_file():
        raise ValueError('当前旧接入缺少受限客户端脚本或 PowerShell 7')
    result = subprocess.run([pwsh, '-NoProfile', '-NonInteractive', '-File', str(script), *arguments],
                            capture_output=True, text=True, encoding='utf-8', timeout=timeout,
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    if result.returncode:
        raise ValueError('服务器操作未完成，请核查原记录；没有自动重试')
    report = json.loads(result.stdout.lstrip('\ufeff'))
    return report


def refresh_history(config):
    if config['driver'] != 'restricted':
        return
    if (DATA / 'sender-migration-pending.json').exists():
        raise ValueError('当前旧接入的发件规则正在同步，暂不能发送')
    report = _legacy_call('audit', config, '-Operation', 'recent_outbound')
    if not report.get('ok') or 'lines' not in report:
        raise ValueError('无法读取已有服务器发信历史，暂停发送')
    (DATA / 'server-history.json').write_text(json.dumps(report, ensure_ascii=False), encoding='utf-8')


def _close(client):
    # A local socket cleanup failure must not discard a received acceptance.
    try:
        client.close()
    except (OSError, smtplib.SMTPException):
        pass


def _smtp_client(config):
    context = ssl.create_default_context()
    client = (smtplib.SMTP_SSL(timeout=config['timeout'], context=context)
              if config['security'] == 'ssl' else smtplib.SMTP(timeout=config['timeout']))
    try:
        client.connect(config['host'], config['port'])
        client.ehlo_or_helo_if_needed()
        if config['security'] == 'starttls':
            client.starttls(context=context)
            client.ehlo()
        if config['use_auth']:
            client.login(config['username'], config['password'])
        return client
    except Exception:
        _close(client)
        raise


def check_connection():
    with locked('mail'):
        config = require_ready()
        try:
            if config['driver'] == 'restricted':
                report = _legacy_call('audit', config, '-Operation', 'health')
                if not report.get('ok'):
                    raise ValueError('服务器只读检查未通过')
            else:
                client = _smtp_client(config)
                _close(client)
        except (OSError, smtplib.SMTPException, subprocess.SubprocessError):
            raise ValueError('连接检查未通过，请核对服务器、端口、加密方式、账号和授权码。未发送邮件。') from None
    return '邮件服务器连接检查通过。此检查只连接和认证，没有发送邮件，也不确认最终投递。'


def submit(config, path, recipient, message_id):
    """Exactly one envelope/DATA attempt. An uncertain attempt must never be retried."""
    uncertain = ('uncertain', '提交结果不明，请按原 Message-ID 核查邮件服务器记录，勿重发。')
    if config['driver'] == 'restricted':
        try:
            ack = _legacy_call('send', config, '-MessagePath', str(path), '-ExpectedRecipient', recipient, timeout=50)
            if ack.get('wrapper_accepted') is True and ack.get('recipient') == recipient and ack.get('message_id') == message_id:
                return 'accepted', '服务器已接收；对方服务器的最终投递状态尚未核查。'
        except (ValueError, OSError, subprocess.SubprocessError):
            pass
        return uncertain
    client = None
    try:
        client = _smtp_client(config)
        refused = client.sendmail(config['sender_email'], [recipient], path.read_bytes())
        if refused:
            return 'rejected', 'SMTP 服务器拒绝收件人；未自动重试。请核查原提交记录。'
        return 'accepted', 'SMTP 服务器已接收此封邮件；尚未确认对方服务器或收件箱的投递结果。'
    except smtplib.SMTPResponseException as exc:
        return 'rejected', f'SMTP 服务器拒绝提交（状态码 {exc.smtp_code}）；未自动重试。'
    except smtplib.SMTPRecipientsRefused:
        return 'rejected', 'SMTP 服务器拒绝收件人；未自动重试。'
    except smtplib.SMTPNotSupportedError:
        return 'rejected', 'SMTP 服务器不支持所选加密或认证方式；未提交邮件，也未自动重试。'
    except (OSError, smtplib.SMTPException):
        return uncertain
    finally:
        if client is not None:
            _close(client)


def audit_legacy(config, message_id):
    return _legacy_call('audit', config, '-Operation', 'lookup', '-Needle', message_id)
