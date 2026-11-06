"""Register only approved Chinese excerpts and complete public AU6/IR4 sources."""
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from scripts.compile_nist_source_units import compile_units  # noqa: E402


def read(p):
    return json.loads(p.read_text(encoding='utf8'))


def write(p, v):
    p.write_text(json.dumps(v, ensure_ascii=False, indent=2)+'\n', encoding='utf8')


def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


old = ROOT / 'data/review_packets/nist-mixed-cards-regression-20261107-r2'
original = ROOT / 'data/review_packets/nist-mixed-private-20261104'
packet = ROOT / 'data/review_packets/nist-mixed-source-bound-20261107'
statefile = HERE / 'continuation.json'
state = read(statefile)
b = state['budget']
assert not b['pending_allocations'] and b['accounted_with_reservations_usd']+.08 <= 3
assert sha(original/'outgoing-preview.json') == read(original/'approval-scope.json')['outgoing_preview_sha256']
packet.mkdir(exist_ok=False)
for name in ('chunks.json', 'prompts.json', 'card-selector.json', 'scope-proof.json'):
    shutil.copyfile(old/name, packet/name)
shutil.copyfile(ROOT/'scripts/answer_unit_contract.py', packet/'contract.py')
shutil.copyfile(HERE/'bilingual-units-r3.json', packet/'translations.json')
shutil.copyfile(HERE/'translation-review-r3.json', packet/'translation-review.json')
sources = read(old/'source-units.json')
sources['sources'] = [s for s in sources['sources'] if s['locator'] not in {'ac-2', 'ia-2'}]
translations = read(packet/'translations.json')
previews = read(original/'outgoing-preview.json')
private = []
for row in previews:
    context = row['contexts'][1]
    assert context['private_course']
    private.append({key: context.get(key) for key in ('source', 'page', 'text', 'source_url', 'locator')})
compiled = compile_units(private)
for source, context in zip(compiled['sources'], private, strict=True):
    assert source['canonical_text_sha256'] == hashlib.sha256(context['text'].encode()).hexdigest()
    source['page'] = context['page']
    for unit in source['units']:
        if unit['text'].strip():
            translations['units'][unit['id']] = {'english_sha256': hashlib.sha256(unit['text'].encode()).hexdigest(),
                'reading_translation': unit['text'], 'original_language': 'zh'}
sources['sources'].extend(compiled['sources'])
sources.update(canonical_sources=len(sources['sources']), scope='Public complete6 sources plus only approved4 private full excerpts, Chinese spans verbatim; no questions/labels/answers.')
write(packet/'source-units.json', sources)
write(packet/'translations.json', translations)
cases = read(old/'cases.json')
for case in cases:
    case['expected_card_path'] = case['target_locator'] in {'au-6', 'ir-4'}
write(packet/'cases.json', cases)
dependencies = read(old/'runtime-source-hashes.json')
write(packet/'runtime-source-hashes.json', {name: sha(ROOT/name) for name in dependencies})
write(packet/'funding-preflight.json', {'prior_accounted_usd': b['accounted_with_reservations_usd'],
    'unknown_reservations': b['unknown_fee_reservations'], 'allocation_usd': .08, 'max_calls': 8,
    'max_accounted_usd': b['accounted_with_reservations_usd']+.08, 'standing_budget_usd': 3})
write(packet/'delta.json', {'same_cases_questions_and_criteria': True, 'same_chunks_sha256': sha(old/'chunks.json'),
    'source_bound_paths': ['au-6', 'ir-4'], 'other2_fallback_unchanged': True,
    'new_private_fragments': 0, 'native_Chinese_units': sum(len(s['units']) for s in compiled['sources']),
    'new_public_translated_units': 23, 'no_new_model_prompt_rules': True,
    'normal_Chinese_source_display': 'verbatim original, no claim to be English or AI translation'})
m = read(old/'manifest.json')
m.update(executor_sha256=sha(ROOT/'scripts/evaluate_public_http.py'),
    role='Same approvedmixed4, source-bound renderer for completeAU6/IR4 plus approvedChinese spans; original2 fallback; no prompt override.',
    acceptance='Same original source/conditions/terms/scope criteria, exact approvedfullTop2, all4 strictaudit and AIreview; no human gold.')
m['frozen_sha256'] = {p.name: sha(p) for p in packet.iterdir() if p.is_file()}
write(packet/'manifest.json', m)
write(packet/'authorization.json', {'max_calls': 8, 'budget_usd': .08, 'manifest_sha256': sha(packet/'manifest.json')})
output = 'docs/engineering-delivery/evaluation-window2-20261107/operator-private/mixed-source-bound-live'
state['active_experiment'] = {'packet': packet.relative_to(ROOT).as_posix(), 'output': output,
    'status': 'frozen_source_bound_AU6_IR4_with_same_private_scope', 'max_calls': 8, 'budget_usd': .08}
state['pending_batches'] = [state['active_experiment']]
b['pending_allocations'] = [{'batch': packet.name, 'max_calls': 8, 'budget_usd': .08}]
write(statefile, state)
controller = (HERE/'dispatch-mixed-regression-r2.py').read_text(encoding='utf8').replace(
    'nist-mixed-cards-regression-20261107-r2', 'nist-mixed-source-bound-20261107').replace(
    'mixed-cards-regression-r2-live', 'mixed-source-bound-live').replace(
    'mixed-cards-r2-console.log', 'mixed-source-bound-console.log')
(HERE/'dispatch-mixed-source-bound.py').write_text(controller, encoding='utf8')
write(HERE/'mixed-source-bound-preflight.json', {'same_private_scope': True, 'new_private_fragments': 0,
    'cases': 4, 'source_bound_targets': 2, 'fallback_targets': 2, 'max_calls': 8, 'budget_usd': .08,
    'max_accounted_usd': b['accounted_with_reservations_usd']+.08, 'source_packet_local_only': True,
    'source_units_sha256': sha(packet/'source-units.json'), 'chunks_sha256': sha(packet/'chunks.json')})
print(json.dumps({'cases': 4, 'source_bound_targets': 2, 'max_calls': 8, 'max_accounted_usd': b['accounted_with_reservations_usd']+.08}))
