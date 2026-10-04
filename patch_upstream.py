"""Apply local workbench source changes; upstream licences remain intact."""
import ast
from pathlib import Path
ROOT=Path(__file__).resolve().parent/'src'

def guard(path, functions):
    content=path.read_text(encoding='utf-8')
    tree=ast.parse(content)
    lines=content.splitlines(keepends=True)
    inserts=[]
    for node in tree.body:
        if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in functions:
            if 'BedSetCo policy' in ast.get_source_segment(content,node):
                continue
            first=node.body[0]
            at=first.end_lineno if isinstance(first,ast.Expr) and isinstance(first.value,ast.Constant) and isinstance(first.value.value,str) else first.lineno-1
            inserts.append((at,'    # BedSetCo policy: local contacts, no central hub or autonomous mail.\n'+functions[node.name]+'\n'))
    for at,text in sorted(inserts,reverse=True):
        lines.insert(at,text)
    path.write_text(''.join(lines),encoding='utf-8')

hub=ROOT/'OpenOutFind/openoutfind/contacts/service.py'
guard(hub,{'token_in_hand':'    return ""','resolve':'    return None',
    'contribute':'    return None','share_profiles':'    return None',
    'register_operator':'    return False','hub_balance':'    return {"balance": None, "known": False}',
    '_register_this_install':'    return ""','_register':'    return None','_mint':'    return ""',
    '_send':'    return None'})
guard(ROOT/'OpenOutreach/openoutreach/wizard.py',{
    '_newsletter_default':'    return False',
    'apply_to_environment':'    config.newsletter = False\n    os.environ["OPENOUTFIND_NEWSLETTER"] = "false"',
    '_write_back_from_environment':'    config.newsletter = False\n    os.environ["OPENOUTFIND_NEWSLETTER"] = "false"'})
guard(ROOT/'OpenOutreach/openoutreach/__main__.py',{
    '_send':'    raise SystemExit("Use the local reviewed queue (outreach.py) with your own mail settings.")',
    '_run':'    raise SystemExit("Use the local workbench (outreach.py) to collect, review and send.")'})
guard(ROOT/'OpenOutSend/cold_outreach/emails/sender.py',{
    '_attribute':'    return body.rstrip() + "\\n"',
    '_deliver':'    raise RuntimeError("Send through the reviewed local queue with your configured mail server.")'})
print('Local workbench source changes applied.')

# pydantic-ai 2 uses OpenAIChatModel; upstream still imports the removed alias.
for path in (ROOT/'OpenOutFind/openoutfind/core/llm.py', ROOT/'OpenOutSend/cold_outreach/core/llm.py'):
    source=path.read_text(encoding='utf-8')
    path.write_text(source.replace('OpenAIModel','OpenAIChatModel'),encoding='utf-8')
