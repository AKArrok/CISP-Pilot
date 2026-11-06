"""Independent same-input repetition; never overwrite or remove previous locks."""
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


old = ROOT / 'data/review_packets/nist-mixed-source-bound-ac2-20261107'
packet = old.with_name(old.name+'-repeat')
statefile = HERE/'continuation.json'
state = read(statefile)
b = state['budget']
assert not b['pending_allocations'] and b['accounted_with_reservations_usd']+.08 <= 3
assert read(HERE/'operator-private/mixed-source-bound-ac2-live/audit.json')['passed']
packet.mkdir(exist_ok=False)
for p in old.iterdir():
    if p.is_file() and p.name not in {'manifest.json', 'authorization.json', 'dispatch.lock', 'funding-preflight.json'}:
        shutil.copyfile(p, packet/p.name)
write(packet/'funding-preflight.json', {'prior_accounted_usd': b['accounted_with_reservations_usd'],
    'unknown_reservations': b['unknown_fee_reservations'], 'allocation_usd': .08, 'max_calls': 8,
    'max_accounted_usd': b['accounted_with_reservations_usd']+.08, 'standing_budget_usd': 3})
m = read(old/'manifest.json')
m['role'] += ' Independent same-input repetition; old4/4 retained.'
m['frozen_sha256'] = {p.name: sha(p) for p in packet.iterdir() if p.is_file()}
write(packet/'manifest.json', m)
write(packet/'authorization.json', {'max_calls': 8, 'budget_usd': .08, 'manifest_sha256': sha(packet/'manifest.json')})
state['active_experiment'] = {'packet': packet.relative_to(ROOT).as_posix(),
    'output': 'docs/engineering-delivery/evaluation-window2-20261107/operator-private/mixed-source-bound-ac2-repeat-live',
    'status': 'frozen_same4_repetition', 'max_calls': 8, 'budget_usd': .08}
state['pending_batches'] = [state['active_experiment']]
b['pending_allocations'] = [{'batch': packet.name, 'max_calls': 8, 'budget_usd': .08}]
write(statefile, state)
controller = (HERE/'dispatch-mixed-source-ac2.py').read_text(encoding='utf8').replace(
    'nist-mixed-source-bound-ac2-20261107', 'nist-mixed-source-bound-ac2-20261107-repeat').replace(
    'mixed-source-bound-ac2-live', 'mixed-source-bound-ac2-repeat-live').replace(
    'mixed-source-bound-ac2-console.log', 'mixed-source-bound-ac2-repeat-console.log')
(HERE/'dispatch-mixed-source-ac2-repeat.py').write_text(controller, encoding='utf8')
print(json.dumps({'max_calls': 8, 'max_accounted_usd': b['accounted_with_reservations_usd']+.08}))
