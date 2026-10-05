"""HTML editor for category-specific business settings."""
import html
import business_profiles as businesses


def editor(state, profile, token):
    esc = html.escape
    hidden = (f'<input type="hidden" name="token" value="{token}">'
              f'<input type="hidden" name="profile_id" value="{esc(profile["id"],quote=True)}">'
              f'<input type="hidden" name="profile_revision" value="{profile["revision"]}">')

    def field(key, label, multiline=False, hint=''):
        value = esc(profile.get(key, ''), quote=True)
        control = (f'<textarea name="{key}">{value}</textarea>' if multiline else
                   f'<input name="{key}" value="{value}">')
        return f'<label>{label}{control}</label>' + (f'<small>{hint}</small>' if hint else '')

    options = ''.join(
        f'<option value="{esc(p["id"],quote=True)}"'
        f'{" selected" if p["id"] == profile["id"] else ""}>'
        f'{esc(p["name"])}{" · 每日启用" if p["daily_enabled"] else ""}</option>'
        for p in state['profiles'])
    errors = businesses.missing(profile)
    readiness = ('待完善：' + '、'.join(errors)) if errors else '业务配置完整，可采集客户和生成开发信。'
    auto = ' checked' if profile['daily_enabled'] else ''
    language = ''.join(f'<option value="{key}"{" selected" if profile["language"] == key else ""}>{label}</option>'
                       for key, label in [('en', '英文'), ('zh', '中文')])
    variables = '、'.join('{' + name + '}' for name in businesses.VARIABLES)
    preview = ''
    if not errors:
        try:
            subject, body = businesses.render(profile, 'Example Company')
            preview = (f'<details><summary>查看当前邮件模板预览</summary><h3>{esc(subject)}</h3>'
                       f'<pre>{esc(body)}</pre></details>')
        except ValueError as exc:
            readiness = str(exc)
    searches = esc(businesses.generated_queries(profile))
    return f'''<section class="business"><h2>业务与品类配置</h2>
    <p>每个业务独立保存产品、客户画像、搜索条件和开发信模板。品类可自由填写，例如服装、汽配、电子配件、家居用品或其他产品。</p>
    <form method="post"><input type="hidden" name="token" value="{token}">
    <label>当前手动业务<select name="profile_id">{options}</select></label>
    <button name="action" value="profile_select">切换业务</button></form>
    <form method="post"><input type="hidden" name="token" value="{token}">
    <label>新增业务名称<input name="new_profile_name" placeholder="例如：汽配出口 / 美容工具 / 家居用品" required maxlength="100"></label>
    <button name="action" value="profile_create">新增独立业务</button></form>
    <p class="notice">当前：{esc(profile['name'])} · 版本 {profile['revision']} · {esc(readiness)}</p>
    <form method="post">{hidden}<h3>公司和产品</h3>
    {field('name','业务名称')}{field('brand','公司／品牌名称')}
    {field('category','产品品类（直接用于开发信，请使用收件人能看懂的语言）')}
    {field('search_terms','搜索用产品关键词（每行一组，海外市场建议英文）',True)}
    {field('product_intro','已确认的产品介绍（将用于邮件正文）',True,'只填写你实际经营和能供应的产品。不要把其他品类或款式的参数套用到这里。')}
    {field('product_facts','已确认的补充产品事实（可选，供自动任务审核参考）',True)}
    {field('website','公司官网（可选，完整 https:// 地址）')}
    {field('sender_name','署名姓名／发件显示名称')}
    <label>开发信语言<select name="language">{language}</select></label>
    <h3>目标客户</h3>
    {field('target_markets','目标国家／地区（每行一个）',True)}
    {field('buyer_types','目标客户类型（每行一类，例如 retailers、distributors、importers）',True)}
    {field('buyer_role','优先联系职位／采购角色')}
    {field('target','其他客户筛选条件（可选）',True)}
    {field('exclude_countries','排除国家／地区（可选，每行一个）',True)}
    {field('exclude_industries','排除行业／客户类型（可选，每行一个）',True)}
    <details><summary>高级搜索设置</summary>
    {field('queries','自定义搜索词（可选，每行一组，最多 4 组；留空自动组合当前品类、市场和客户类型）',True)}
    <p>自动组合示例：</p><pre>{searches or '完善品类、市场和客户类型后自动生成。'}</pre>
    {field('websites','候选公司官网（可选，每行一个完整 https:// 地址，最多 30 个）',True)}</details>
    <details><summary>开发信模板</summary><p>可用变量：{esc(variables)}。公司、联系人、产品及署名会按本业务填写。</p>
    {field('subject_template','主题模板')}{field('body_template','正文模板',True)}
    <p>保留回复 unsubscribe 的退出说明。修改业务配置只影响新草稿；历史客户和已提交邮件保留原始业务记录。</p></details>
    <label class="check"><input type="checkbox" name="daily_enabled"{auto}>加入每日自动开发</label>
    <p>已配置的每日任务按「每日自动开发」中保存的数量轮流采集和审核发送。新安装需另行配置任务；勾选此项不会立即发信。</p>
    <button name="action" value="profile_save">保存当前业务配置</button></form>
    <form method="post">{hidden}<button name="action" value="profile_templates">按已保存的语言恢复默认邮件模板</button></form>
    {preview}</section>'''
