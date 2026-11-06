"""Freeze same eight development boundaries through real retrieval/application."""
import hashlib
import json
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
def read(p):
    return json.loads(p.read_text(encoding='utf8'))
def write(p, obj):
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()
source = ROOT / 'evaluation/nist-public-http-cards-v3r2-module-20261107'
old = ROOT / 'evaluation/nist-unit-cards-negatives-v1-20261107'
packet = ROOT / 'evaluation/nist-public-http-cards-boundaries-20261107'
statefile = HERE / 'continuation.json'
state = read(statefile)
b = state['budget']
assert not b['pending_allocations'] and b['accounted_with_reservations_usd'] + .08 <= 3
assert read(ROOT / 'docs/engineering-delivery/nist-public-http-cards-v3r2-module-20261107-live/audit.json')['passed']
assert all(sha(ROOT/name) == h for name,h in read(source/'runtime-source-hashes.json').items())
packet.mkdir(exist_ok=False)
for name in ('card-selector.json','chunks.json','contract.py','prompts.json','runtime-source-hashes.json','source-units.json','translation-review.json','translations.json'):
    shutil.copyfile(source/name,packet/name)
acceptance = read(old/'acceptance.json')
criteria = {c['id']:c for c in acceptance['cases']}
cases = []
for c in read(old/'cases.json'):
    cases.append({key:c[key] for key in ('id','question','target_control','role','thread_group')})
    cases[-1].update(target_locator=c['target_control'], reference_evidence=criteria[c['id']]['original_evidence'],
                    reference_origin='same public pinned original; development, no human gold')
assert len(cases) == 8
write(packet/'cases.json',cases)
shutil.copyfile(old/'acceptance.json',packet/'acceptance.json')
write(packet/'funding-preflight.json',{'prior_accounted_usd':b['accounted_with_reservations_usd'],
    'unknown_fee_reservations':b['unknown_fee_reservations'], 'new_allocation_usd':.08,
    'max_accounted_usd':b['accounted_with_reservations_usd']+.08,'approved_total_usd':3,
    'scope':'same original8 boundary questions/standards; public NIST only; no model/index reference answers'})
manifest = read(source/'manifest.json')
manifest.update(max_calls=16,budget_usd=.08,role='Same boundary8 through actual app retrieval/route/selector/SSE/SQL. Prior fixedTop2 outputs retained; no labels to model.',
                original_cases_sha256=sha(old/'cases.json'),acceptance='Original acceptance.json semantics unchanged; target misses count failure even with safe refusal. All8 strict audit plus AI source review; not human gold.')
manifest['frozen_sha256'] = {p.name:sha(p) for p in packet.iterdir() if p.is_file()}
write(packet/'manifest.json',manifest)
write(packet/'authorization.json',{'max_calls':16,'budget_usd':.08,'manifest_sha256':sha(packet/'manifest.json')})
state['public32_cards']={'status':'strict_audit_passed32_and_Codex_semantic_supported32_not_human_gold',
    'run':'docs/engineering-delivery/nist-public-http-cards-v3r2-module-20261107-live', 'default_promoted':False}
state['active_experiment']={'packet':packet.relative_to(ROOT).as_posix(),
    'output':'docs/engineering-delivery/nist-public-http-cards-boundaries-20261107-live','status':'frozen', 'max_calls':16,'budget_usd':.08}
state['pending_batches']=[state['active_experiment']]
b['pending_allocations']=[{'batch':packet.name,'max_calls':16,'budget_usd':.08}]
state['quota']['last_verified_primary_remaining_percent']=32
state['next']='Complete boundary8 actual app, then same authorized4 private mixed regression. No default promotion or independent-human claim.'
write(statefile,state)
controller=(HERE/'dispatch-http32-module.py').read_text(encoding='utf8')
controller=controller.replace('nist-public-http-cards-v3r2-module-20261107','nist-public-http-cards-boundaries-20261107').replace('64','16').replace('.20','.08').replace('http32-module-console.log','http-boundaries-console.log')
# Constant replacement is restricted to this archived controller; source hashes remain loaded from the packet.
assert 'max_calls"] == 16' in controller
(HERE/'dispatch-http-boundaries.py').write_text(controller,encoding='utf8')
print(json.dumps({'cases':len(cases),'max_calls':16,'budget_usd':.08,'max_accounted_usd':b['accounted_with_reservations_usd']+.08}))
