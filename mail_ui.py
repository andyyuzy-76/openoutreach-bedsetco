"""User-facing SMTP settings; password values are never rendered."""
import html
import os

import mail_transport as mail


def editor(config, token):
    esc = html.escape
    hidden = (f'<input type="hidden" name="token" value="{token}">'
              f'<input type="hidden" name="mail_revision" value="{config["revision"]}">')

    def field(key, label, kind='text', bounds=''):
        return (f'<label>{label}<input type="{kind}" name="{key}" '
                f'value="{esc(str(config.get(key, "")),quote=True)}" {bounds}></label>')

    modes = [('starttls', 'STARTTLS（常用端口 587）'), ('ssl', 'SSL/TLS（常用端口 465）'),
             ('none', '不加密（仅用于无需账号认证的中继）')]
    security = ''.join(f'<option value="{value}"{" selected" if config["security"] == value else ""}>'
                       f'{label}</option>' for value, label in modes)
    driver = '<input type="hidden" name="driver" value="smtp">'
    if config['driver'] == 'restricted':
        driver = ('<label>接入方式<select name="driver"><option value="restricted" selected>'
                  '当前已配置的服务器接入</option><option value="smtp">自行配置 SMTP 服务器</option></select></label>')
    password_status = ('使用进程环境中的密码，页面保存不会改写环境变量。' if 'OUTREACH_SMTP_PASSWORD' in os.environ
                       else '密码／授权码已保存，页面不回显。' if config['password'] else '尚未保存 SMTP 密码／授权码。')
    ready = bool(config['sender_email'] and (config['driver'] == 'restricted' or config['host']))
    status = ('已保存邮件接入配置，连接情况可单独检查。' if ready else '尚未配置；填写自己的邮箱服务器后才能审核和发送。')
    return f'''<section><h2>邮件服务器 · 自行配置</h2>
    <p>{esc(status)} 支持自建邮件服务器和提供 SMTP 的邮箱服务。</p>
    <form method="post">{hidden}{driver}
    {field('host','SMTP 服务器（主机名或 IP，例如 smtp.example.com）')}
    {field('port','SMTP 端口','number','min="1" max="65535"')}
    <label>连接加密<select name="security">{security}</select></label>
    <label class="check"><input type="checkbox" name="use_auth"{" checked" if config['use_auth'] else ""}>需要账号认证</label>
    {field('username','SMTP 用户名（通常是完整邮箱）')}
    <label>SMTP 密码／授权码（留空保留；更换服务器或账号时须重新填写）
    <input type="password" name="smtp_password" autocomplete="new-password"></label>
    <small>{esc(password_status)}</small>
    <label class="check"><input type="checkbox" name="clear_password">清除已保存密码</label>
    {field('sender_email','发件邮箱（From，须为服务器允许使用的地址）','email')}
    {field('reply_to','回复邮箱（Reply-To，留空使用发件邮箱）','email')}
    {field('display_name','发件显示名称（可选，留空使用对应业务的署名）')}
    {field('daily_limit','所有业务合计每日提交上限','number','min="1" max="10000"')}
    {field('interval_seconds','两次邮件提交的最小间隔（秒）','number','min="0" max="86400"')}
    {field('timeout','连接与通信超时（秒）','number','min="5" max="120"')}
    <button name="action" value="mail_save">保存邮件服务器</button></form>
    <form method="post">{hidden}<button name="action" value="mail_check">检查已保存配置的连接（不发信）</button></form>
    <p>保存只更新配置。发件邮箱会用于邮件头和模板里的 {{sender_email}}。
    修改邮件配置后，未发送的草稿须点击“按当前邮件配置重建草稿”，再审核；历史已提交邮件保留原始信息。</p>
    <p>SMTP 接受邮件后标为“服务器已接收”，最终投递和退信需在自己的邮箱或服务器中核查。</p></section>'''
