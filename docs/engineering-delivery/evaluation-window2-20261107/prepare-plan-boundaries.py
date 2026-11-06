"""Freeze adjacent public boundaries before execution; criteria never model input."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent


def read(p):
    return json.loads(p.read_text(encoding='utf8'))


def write(p, v):
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n', encoding='utf8')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


source = ROOT/'evaluation/nist-public-http-cards-v3r2-module-20261107'
packet = ROOT/'evaluation/nist-public-http-plan-boundaries-20261107'
statefile = HERE/'continuation.json'
state = read(statefile)
b = state['budget']
assert not b['pending_allocations'] and b['accounted_with_reservations_usd']+.08 <= 3
packet.mkdir(exist_ok=False)
for name in ('cases.json', 'chunks.json', 'prompts.json', 'card-selector.json', 'source-units.json'):
    if name != 'cases.json':
        shutil.copyfile(source/name, packet/name)
shutil.copyfile(ROOT/'scripts/answer_unit_contract.py', packet/'contract.py')
shutil.copyfile(HERE/'bilingual-units-r4.json', packet/'translations.json')
shutil.copyfile(HERE/'translation-review-r4.json', packet/'translation-review.json')
questions = [
    ('ir-4', 'IR-4是否表示只需与应急规划协调，就不必让事件处理能力与事件响应计划一致？', 'deny', '两种关系分开，不能免除与响应计划一致；不能推断必须为两个物理文件。'),
    ('ir-4', 'IR-4是否明确把根除规定为必须重装操作系统并删除所有日志？', 'not_specified', '本次原文只列根除，无此具体步骤/定义，不把未给细节说成整个库不存在。'),
    ('au-6', 'AU-6的审查频次参数未赋值，是否就能停止审计分析和向指定角色报告？', 'deny', '未赋值不免除审查分析/报告动作，频次和接收对象不同。'),
    ('au-6', 'AU-6中本机构无法分析审计记录时，其他组织是否无需获得授权也能代行？', 'deny', '其他组织须granted such authority，不扩大为任意未授权代行。'),
]
texts = {c['locator']: c['text'] for c in read(packet/'chunks.json')}
cases, acceptance = [], []
for index, (locator, question, direction, criterion) in enumerate(questions, 1):
    case_id = f'plan-boundary-{index:02d}'
    cases.append({'id': case_id, 'question': question, 'target_locator': locator,
        'thread_group': case_id, 'reference_evidence': texts[locator],
        'role': 'New failure-directed development boundary, not independent unseen/human gold'})
    acceptance.append({'id': case_id, 'expected_direction': direction, 'criterion': criterion,
        'grading': 'Actual answer/source/conditions, not enum alone. Preserve all old failures.'})
write(packet/'cases.json', cases)
write(packet/'acceptance.json', {'scope': 'Offline only; no index/model expected answers', 'cases': acceptance})
dependencies = read(source/'runtime-source-hashes.json')
write(packet/'runtime-source-hashes.json', {name: sha(ROOT/name) for name in dependencies})
write(packet/'funding-preflight.json', {'prior_accounted_usd': b['accounted_with_reservations_usd'],
    'unknown_reservations': b['unknown_fee_reservations'], 'max_calls': 8, 'allocation_usd': .08,
    'max_accounted_usd': b['accounted_with_reservations_usd']+.08, 'standing_budget_usd': 3})
m = read(source/'manifest.json')
m.update(max_calls=8, budget_usd=.08, executor_sha256=sha(ROOT/'scripts/evaluate_public_http.py'),
    role='Four adjacent public AU6/IR4 development boundaries, full original101 corpus and actual app; no private material.',
    original_cases_sha256=None, acceptance='All4 durable strictaudit plus original-source/condition AI review per acceptance.json; no enum gold or independenthuman claim.')
m['frozen_sha256'] = {p.name: sha(p) for p in packet.iterdir() if p.is_file()}
write(packet/'manifest.json', m)
write(packet/'authorization.json', {'max_calls': 8, 'budget_usd': .08, 'manifest_sha256': sha(packet/'manifest.json')})
state['active_experiment'] = {'packet': packet.relative_to(ROOT).as_posix(),
    'output': 'docs/engineering-delivery/nist-public-http-plan-boundaries-20261107-live',
    'status': 'frozen_public_plan_boundaries', 'max_calls': 8, 'budget_usd': .08}
state['pending_batches'] = [state['active_experiment']]
b['pending_allocations'] = [{'batch': packet.name, 'max_calls': 8, 'budget_usd': .08}]
write(statefile, state)
controller = (HERE/'dispatch-http32-module.py').read_text(encoding='utf8').replace(
    'nist-public-http-cards-v3r2-module-20261107', 'nist-public-http-plan-boundaries-20261107').replace(
    '64', '8').replace('.20', '.08').replace('http32-module-console.log', 'plan-boundaries-console.log')
(HERE/'dispatch-plan-boundaries.py').write_text(controller, encoding='utf8')
print(json.dumps({'max_calls': 8, 'max_accounted_usd': b['accounted_with_reservations_usd']+.08}))
