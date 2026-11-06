"""Complete AC2 source translation; no question or expected answer inputs."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
compiled = json.loads((ROOT/'docs/engineering-delivery/evaluation-resume-20261106/literal-source-units.json').read_text(encoding='utf8'))
source = next(s for s in compiled['sources'] if s['locator'] == 'ac-2')
translations = json.loads((HERE/'bilingual-units-r3.json').read_text(encoding='utf8'))
text = [
    '账号管理',
    '定义并记录系统内允许使用和明确禁止使用的账号类型；',
    '指定账号管理员；',
    '对组和角色成员资格要求{{ insert: param, ac-02_odp.01 }}；',
    '规定：',
    '系统的授权用户；',
    '组和角色成员资格；',
    '每个账号的访问授权（即权限）和{{ insert: param, ac-02_odp.02 }}；',
    '账号创建请求须获得{{ insert: param, ac-02_odp.03 }}的批准；',
    '按照{{ insert: param, ac-02_odp.04 }}创建、启用、修改、禁用和移除账号；',
    '监测账号使用；',
    '在下列期限内通知账号管理员和{{ insert: param, ac-02_odp.05 }}：',
    '账号不再需要时，在{{ insert: param, ac-02_odp.06 }}内；',
    '用户离职或调动时，在{{ insert: param, ac-02_odp.07 }}内；',
    '个人的系统使用或知悉需要发生变化时，在{{ insert: param, ac-02_odp.08 }}内；',
    '根据下列事项授权系统访问：',
    '有效的访问授权；',
    '预期的系统使用；',
    '{{ insert: param, ac-02_odp.09 }}；',
    '按{{ insert: param, ac-02_odp.10 }}审查账号是否符合账号管理要求；',
    '在个人被移出组时，建立并实施更改共享或组账号认证凭据的流程（若部署了此类账号）；',
    '将账号管理流程与人员离职和调动流程对齐。',
    '系统账号类型的例子包括个人、共享、组、系统、访客、匿名、应急、开发者、临时和服务账号。',
    '识别授权系统用户以及规定访问权限，反映安全计划中其他控制的要求。',
    '需要系统账号管理权限的用户，由负责批准此类账号和特权访问的组织人员进行额外审查，包括系统所有者、使命或业务所有者、机构高级信息安全官或机构高级隐私官。',
    '组织可能因风险增加而希望禁止的账号类型包括共享、组、应急、匿名、临时和访客账号。',
    '当访问涉及个人可识别信息时，安全项目与机构高级隐私官合作：制定组和角色成员资格的具体条件；规定每个账号的授权用户、组和角色成员资格以及访问授权；并按组织政策创建、调整或移除系统账号。',
    '政策可包含账号到期日期或其他触发禁用账号的因素等信息。',
    '组织可选择按账号、账号类型或二者结合来定义访问权限或其他属性。',
    '授权访问所需其他属性的例子包括对每天的时段、每周的日期和来源位置的限制。',
    '定义其他系统账号属性时，组织考虑系统相关要求以及使命／业务要求。',
    '未考虑这些因素可能影响系统可用性。',
    '临时和应急账号旨在短期使用。',
    '需要短期账号但不要求立即激活账号时，组织把建立临时账号纳入正常账号激活程序。',
    '组织为应对危机情况以及迅速激活账号的需要而建立应急账号。',
    '因此，应急账号的激活可绕过正常账号授权流程。',
    '不要把应急和临时账号与不常使用的账号混淆；后者包括用于特殊任务或网络资源不可用时的本地登录账号，也可称为最后手段账号。',
    '此类账号保持可用，不受自动禁用或移除日期的限制。',
    '禁用或停用账号的条件包括共享／组、应急或临时账号不再需要，以及人员调动或离职。',
    '成员离开组时更改共享／组认证凭据，旨在确保前组成员不再保留对共享或组账号的访问。',
    '某些系统账号类型可能需要专门培训。',
    '原生OSCAL参数定义（本次提取未赋值）：',
]
definitions = json.loads(source['units'][-1]['text'])
parameter_text = [
    '组和角色成员资格的前提与准则，待定义。', '每个账号所需的属性，待定义。',
    '批准创建账号请求的人员或角色，待定义。', '创建、启用、修改、禁用及移除账号的政策、程序、前提与准则，待定义。',
    '需通知的人员或角色，待定义。', '账号不再需要时通知账号管理员的期限，待定义。',
    '用户离职或调动时通知账号管理员的期限，待定义。', '个人系统使用或知悉需要变化时通知账号管理员的期限，待定义。',
    '授权系统访问所需的属性，待定义。', '账号审查的频次，待定义。',
]
text.append('\n'.join(d['id']+'：'+t for d, t in zip(definitions, parameter_text, strict=True)))
units = [u for u in source['units'] if u['text'].strip()]
review = []
for unit, chinese in zip(units, text, strict=True):
    translations['units'][unit['id']] = {'english_sha256': hashlib.sha256(unit['text'].encode()).hexdigest(), 'reading_translation': chinese}
    review.append({'id': unit['id'], 'english': unit['text'], 'reading_translation': chinese})
for definition, chinese in zip(definitions, parameter_text, strict=True):
    translations['parameters'][definition['id']] = {'original_definition': definition,
        'source_text_sha256': source['canonical_text_sha256'], 'reading_translation': chinese}
assert not (HERE/'bilingual-units-r4.json').exists()
(HERE/'bilingual-units-r4.json').write_text(json.dumps(translations, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
(HERE/'translation-review-r4.json').write_text(json.dumps({'role': 'Source-only Codex AI alignment, not human/NIST official translation',
    'source': 'complete ac-2', 'new_units': review, 'new_parameters': 10, 'questions_labels_answers': False,
    'authenticator': '认证凭据; no password example added', 'normative_strength_preserved': True}, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
print(json.dumps({'nonblank_units': len(units), 'parameters': len(definitions), 'paid_calls': 0}))
