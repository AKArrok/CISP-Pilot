"""Replay saved selectors; prove only banner text changed, not new model quality."""
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from agents.sources import source_preview  # noqa: E402


def read(p):
    return json.loads(p.read_text(encoding='utf8'))


snapshot = HERE/'renderer-replay-contract.py'
assert not snapshot.exists()
shutil.copyfile(ROOT/'scripts/answer_unit_contract.py', snapshot)
spec = importlib.util.spec_from_file_location('renderer_replay_40', snapshot)
contract = importlib.util.module_from_spec(spec)
spec.loader.exec_module(contract)
cases = []
for name in ['nist-public-http-cards-v3r2-module-20261107', 'nist-public-http-cards-boundaries-20261107']:
    packet = ROOT/'evaluation'/name
    run = ROOT/'docs/engineering-delivery'/(name+'-live')
    chunks = read(packet/'chunks.json')
    sources = read(packet/'source-units.json')['sources']
    translations = read(packet/'translations.json')
    receipts = [json.loads(line) for line in (run/'receipts.jsonl').read_text(encoding='utf8').splitlines()]
    answers = [r for r in receipts if r['stage'] == 'answer']
    for row, receipt in zip(read(run/'results.json'), answers, strict=True):
        previews = next(e['contexts'] for e in row['events'] if e['type'] == 'contexts')
        contexts = [next(c for c in chunks if source_preview(c) == p) for p in previews]
        raw = receipt['response']['generations'][0][0]['message']['content']
        selected = contract.materialize_card_selection(raw, contract.build_catalog(contexts, sources), contexts)
        rendered = contract.render_card_answer(selected, contexts, translations, question=row['question'])
        previous = ''.join(e.get('content', '') for e in row['events'] if e['type'] == 'token')
        before = '以下辅助译文由AI核对，属于非官方译文；英文原文为来源依据。'
        after = '辅助译文由AI核对，属于非官方译文；引用的原文为来源依据。'
        unchanged = previous.replace(before, after) == rendered
        assert unchanged, row['id']
        cases.append({'id': row['id'], 'original_run': name, 'body_unchanged_except_banner': unchanged,
                      'selector_sha256': hashlib.sha256(raw.encode()).hexdigest(),
                      'previous_answer_sha256': hashlib.sha256(previous.encode()).hexdigest(),
                      'replay_answer_sha256': hashlib.sha256(rendered.encode()).hexdigest()})
result = {'mode': 'Offline replay of saved real selectors, no new app/HTTP/model execution',
    'cases': cases, 'render_succeeded': len(cases), 'body_unchanged_except_banner': len(cases),
    'model_calls': 0, 'cost_usd': 0, 'new_semantic_score_claimed': False,
    'snapshot_sha256': hashlib.sha256(snapshot.read_bytes()).hexdigest(),
    'direct_dependency_sha256': {name: hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in
        ['scripts/answer_output_contract.py', 'scripts/inline_oscal_parameters.py', 'agents/sources.py']}}
with (HERE/'renderer-replay-40.json').open('x', encoding='utf8') as stream:
    json.dump(result, stream, ensure_ascii=False, indent=2)
    stream.write('\n')
print(json.dumps({'offline_rendered': len(cases), 'body_unchanged_except_banner': len(cases), 'new_calls': 0}))
