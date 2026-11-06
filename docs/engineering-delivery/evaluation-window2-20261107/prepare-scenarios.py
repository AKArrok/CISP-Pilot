"""Freeze adjacent public boundaries before execution; criteria never model input."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
HERE = Path(__file__).resolve().parent


def read(p):
    return json.loads(p.read_text(encoding='utf8'))


def write(p, v):
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n', encoding='utf8')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


source = ROOT/'evaluation/nist-public-http-cards-v3r2-module-20261107'
packet = ROOT/'evaluation/nist-public-http-scenarios-r2-20261107'
statefile = HERE/'continuation.json'
state = read(statefile)
b = state['budget']
assert not b['pending_allocations'] and b['accounted_with_reservations_usd']+.12 <= 3
packet.mkdir(exist_ok=False)
for name in ('cases.json', 'chunks.json', 'prompts.json', 'card-selector.json', 'source-units.json'):
    if name != 'cases.json':
        shutil.copyfile(source/name, packet/name)
shutil.copyfile(ROOT/'scripts/answer_unit_contract.py', packet/'contract.py')
shutil.copyfile(HERE/'bilingual-units-r4.json', packet/'translations.json')
shutil.copyfile(HERE/'translation-review-r4.json', packet/'translation-review.json')
questions = [
    ('ac-2', '新员工准备加入组和角色并创建账号，口头同意是否足够？资料对成员资格条件和创建请求批准分别怎样要求？', 'explain', '组/角色条件与创建批准分开；具体条件、批准人员未赋值，不编造IT统一批准。'),
    ('ac-8', '公开访问系统能否因为用户继续使用就无视隐私安排，一律加入监控和审计告知？', 'deny', '公开系统监控引用须与隐私安排一致，原文一般禁止，不能全盘允许或绝对全部禁止。'),
    ('at-2', '方案只给新员工入职培训，不安排后续频次或系统变化、相关事件后的培训，这样覆盖了AT-2要求吗？', 'deny', '初始及后续频次、系统变更或组织定义事件后的培训，保留参数未赋值。'),
    ('cp-9', '备份方案只有业务文件，没有操作系统和许可证，也没有安全与隐私相关系统文档，能视为覆盖了CP-9列出的备份对象吗？', 'deny', '用户级/系统级/文档不同，软件许可证属系统级；不编造频次。'),
    ('ca-8', '方案把自动漏洞扫描当作渗透测试的替代，并计划测试结束后再商定交战规则，这与CA-8资料一致吗？', 'deny', '渗透超越扫描且规则测试前商定，不泛化为扫描无价值。'),
    ('au-6', '可信外部情报显著改变风险后，保持原审计分析设置就足够了吗？原文对级别、频率或范围怎样描述？', 'deny', '风险变化调整级别，指南频次范围深度可调整；不可把可调整全部变一律强制。'),
    ('ir-4', '在联邦机构，授权员工为未获授权的目的访问个人可识别信息，是否也可能属于泄露，还是只有外部入侵才算？', 'explain', '保留联邦机构/PII条件，授权用户为授权目的以外使用也在定义内；不能只限外部。'),
    ('ia-2', '人在办公室却通过外部网络连接服务器，这仍是本地访问吗？按IA-2如何区分网络访问与远程访问？', 'deny', '是否用网络与是否外部网络两层区分，物理位置无关，不补未给技术机制。'),
]
texts = {c['locator']: c['text'] for c in read(packet/'chunks.json')}
# Full IA2 original is retrieved but remains original fallback without a reviewed translation.
registered = read(packet/'source-units.json')
registered['sources'] = [x for x in registered['sources'] if x['locator'] != 'ia-2']
write(packet/'source-units.json', registered)
import config  # noqa: E402
from retrieval.hybrid import HybridRetriever  # noqa: E402
from retrieval.bm25 import BM25Index  # noqa: E402
from storage import cache  # noqa: E402
for flag in ('ENABLE_DENSE_RETRIEVAL','ENABLE_RERANKING','ENABLE_QUERY_VARIANTS','ENABLE_FEATURE_RERANKING','ENABLE_LEARNED_RANKING'):
    setattr(config,flag,False)
config.RETRIEVER_K=2
cache._client,cache._live=cache._MemClient(),False
retriever=HybridRetriever.__new__(HybridRetriever)
chunks=read(packet/'chunks.json')
retriever._bm25=BM25Index.build(chunks)
retriever._chunks_by_id={c['id']:c for c in chunks}
retriever._dense_degraded=False
historical=set()
for directory in (ROOT/'evaluation', ROOT/'data/review_packets'):
    for path in directory.glob('*/cases.json'):
        loaded = read(path)
        if isinstance(loaded, dict):
            loaded = loaded.get('cases', [])
        for oldcase in loaded:
            if isinstance(oldcase, dict) and isinstance(oldcase.get('question'), str):
                historical.add(oldcase['question'].strip())
assert all(q[1].strip() not in historical for q in questions)

cases, acceptance = [], []
for index, (locator, question, direction, criterion) in enumerate(questions, 1):
    case_id = f'scenario-{index:02d}'
    contexts = retriever.retrieve(question,top_k=2)
    cases.append({'id': case_id, 'question': question, 'target_locator': locator,
        'expected_card_path': all(c.get('locator') != 'ia-2' for c in contexts),
        'thread_group': case_id, 'reference_evidence': texts[locator],
        'role': 'New failure-directed development boundary, not independent unseen/human gold'})
    acceptance.append({'id': case_id, 'expected_direction': direction, 'criterion': criterion,
        'grading': 'Actual answer/source/conditions, not enum alone. Preserve all old failures.'})
write(packet/'cases.json', cases)
write(packet/'history-check.json', {'historical_exact_questions':len(historical),'new_exact_overlaps':0,'scope':'New compositional development scenarios, same seen source controls; no semantic independence or human gold claim'})
write(packet/'acceptance.json', {'scope': 'Offline only; no index/model expected answers', 'cases': acceptance})
dependencies = read(source/'runtime-source-hashes.json')
write(packet/'runtime-source-hashes.json', {name: sha(ROOT/name) for name in dependencies})
write(packet/'funding-preflight.json', {'prior_accounted_usd': b['accounted_with_reservations_usd'],
    'unknown_reservations': b['unknown_fee_reservations'], 'max_calls': 16, 'allocation_usd': .12,
    'max_accounted_usd': b['accounted_with_reservations_usd']+.12, 'standing_budget_usd': 3})
m = read(source/'manifest.json')
m.update(max_calls=16, budget_usd=.12, executor_sha256=sha(ROOT/'scripts/evaluate_public_http.py'),
    role='Eight compositional public AU6/IR4 development boundaries, full original101 corpus and actual app; no private material.',
    original_cases_sha256=None, acceptance='All8 durable strictaudit plus original-source/condition AI review per acceptance.json; no enum gold or independenthuman claim.')
m['frozen_sha256'] = {p.name: sha(p) for p in packet.iterdir() if p.is_file()}
write(packet/'manifest.json', m)
write(packet/'authorization.json', {'max_calls': 16, 'budget_usd': .12, 'manifest_sha256': sha(packet/'manifest.json')})
state['active_experiment'] = {'packet': packet.relative_to(ROOT).as_posix(),
    'output': 'docs/engineering-delivery/nist-public-http-scenarios-r2-20261107-live',
    'status': 'frozen_public_scenarios', 'max_calls': 16, 'budget_usd': .12}
state['pending_batches'] = [state['active_experiment']]
b['pending_allocations'] = [{'batch': packet.name, 'max_calls': 16, 'budget_usd': .12}]
write(statefile, state)
controller = (HERE/'dispatch-http32-module.py').read_text(encoding='utf8').replace(
    'nist-public-http-cards-v3r2-module-20261107', 'nist-public-http-scenarios-r2-20261107').replace(
    '64', '16').replace('.20', '.12').replace('http32-module-console.log', 'scenarios-console.log')
(HERE/'dispatch-scenarios.py').write_text(controller, encoding='utf8')
print(json.dumps({'max_calls': 16, 'max_accounted_usd': b['accounted_with_reservations_usd']+.12}))
