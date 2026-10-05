"""Editable daily outreach quantity and shared limits."""
import html
import json

import private_store


def editor(config, token, names, submitted, count_error=''):
    esc = html.escape
    schedule_path = private_store.DATA / 'schedule.json'
    if schedule_path.is_file():
        schedule = json.loads(schedule_path.read_text(encoding='utf-8-sig'))
        timezone = schedule.get('timezone', '本机时区')
        if timezone == 'Asia/Shanghai':
            timezone = '北京时间'
        description = (f'已配置的每日任务在{timezone} '
                       f'{schedule.get("time", "09:30")} 运行，轮流采集已启用业务，并按下面保存的数量审核发送。电脑及任务环境须保持可运行。')
    else:
        description = '新安装需自行配置定时采集与审核发送任务；任务按下面的数量执行。启动工作台或保存设置不会创建定时任务。'
    hidden = (f'<input type="hidden" name="token" value="{token}">'
              f'<input type="hidden" name="daily_revision" value="{config["revision"]}">')

    def field(key, label, lower, upper):
        return (f'<label>{label}<input type="number" name="{key}" value="{config[key]}" '
                f'min="{lower}" max="{upper}" step="1" required></label>')

    if submitted is None:
        progress = '今天的提交次数暂时无法读取，任务须恢复台账后再发送。' + count_error
    elif config['daily_target'] == 0:
        progress = f'自动发送已暂停 · 今天已提交 {submitted} 封。'
    else:
        remaining = max(0, config['daily_target'] - submitted)
        progress = f'今天已提交 {submitted} 封 · 自动发送剩余额度 {remaining} 封。'
    return f'''<section id="daily-development"><h2>每日自动开发</h2>
    <p>{esc(description)}</p><p>已启用业务：{esc(names)}</p>
    <form method="post">{hidden}
    {field('daily_target', '每天自动发信数量（封，填 0 暂停自动发送）', 0, 10000)}
    {field('interval_seconds', '发送间隔（秒）', 0, 86400)}
    <details><summary>采集与总量限制</summary>
    {field('new_company_limit', '每日任务最多新增候选公司（所有业务合计）', 1, 10000)}
    {field('daily_limit', '每日合计提交上限（含自动和手动发送）', 1, 10000)}
    <p>上限小于自动发信数量时，保存会同步提高到该数量。候选公司仍须核对后才能发送。</p></details>
    <button name="action" value="daily_save">保存每日设置</button></form>
    <p class="tag">{esc(progress)}</p>
    <p>当前：自动最多 {config['daily_target']} 封／天，合计提交上限 {config['daily_limit']} 封，新增候选上限 {config['new_company_limit']} 家，间隔 {config['interval_seconds']} 秒。</p>
    <p>所有业务共用当天额度，手动提交、失败或结果不明的尝试也计数；改设置不会清零。没有合格客户时可能少于设定数量。
    采集不调用付费数据服务；使用外部 AI 或 Codex 任务时，由所选服务提供额度。</p></section>'''
