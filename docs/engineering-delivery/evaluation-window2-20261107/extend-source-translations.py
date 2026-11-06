"""Source-only AU6/IR4 alignment; no questions, expected answers or labels."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
HERE = Path(__file__).resolve().parent
compiled = json.loads((ROOT / 'docs/engineering-delivery/evaluation-resume-20261106/literal-source-units.json').read_text(encoding='utf8'))
translations = json.loads((HERE / 'bilingual-units-r2.json').read_text(encoding='utf8'))
aligned = {
    'au-6': [
        '审计记录审查、分析与报告',
        '按{{ insert: param, au-06_odp.01 }}审查和分析系统审计记录，查找{{ insert: param, au-06_odp.02 }}的迹象及不当或异常活动的潜在影响；',
        '将发现报告给{{ insert: param, au-06_odp.03 }}；',
        '当基于执法信息、情报信息或其他可信信息来源的风险发生变化时，调整系统内审计记录审查、分析和报告的级别。',
        '审计记录审查、分析和报告覆盖组织开展的信息安全和隐私相关日志记录，包括监测账号使用、远程访问、无线连接、移动设备连接、配置设置、系统组件清单、维护工具使用和非本地维护、物理访问、温湿度、设备交付和移除、系统接口通信，以及移动代码或互联网语音协议（VoIP）使用所产生的日志。',
        '发现可报告给事件响应团队、帮助台、安全或隐私办公室等组织实体。',
        '若组织被禁止审查分析审计记录或无法开展这些活动，可由获授该权限的其他组织开展审查或分析。',
        '可根据收到的新信息调整审计记录审查、分析和报告的频率、范围和／或深度，以满足组织需要。',
        '原生OSCAL参数定义（本次提取未赋值）：',
        'au-06_odp.01：审查和分析系统审计记录的频次，待定义。\nau-06_odp.02：不当或异常活动，待定义。\nau-06_odp.03：接收系统记录审查与分析发现的人员或角色，待定义。',
    ],
    'ir-4': [
        '事件处理',
        '实施与事件响应计划一致的事件处理能力，包含准备、检测与分析、遏制、根除和恢复；',
        '将事件处理活动与应急规划活动协调；',
        '把持续事件处理活动的经验教训纳入事件响应程序、培训和测试，并相应实施由此产生的变更；',
        '确保事件处理活动的严谨性、强度、范围和结果在组织内具有可比性和可预测性。',
        '组织认识到，事件响应能力依赖于组织系统的能力以及这些系统所支持的使命和业务流程。',
        '组织把事件响应纳入使命和业务流程及系统的定义、设计和开发。',
        '事件相关信息可来自多种来源，包括审计监测、物理访问监测和网络监测；用户或管理员报告；以及已报告的供应链事件。',
        '有效的事件处理能力包括多个组织实体之间的协调，例如使命或业务所有者、系统所有者、授权官员、人力资源部门、物理安全部门、人员安全部门、法律部门、风险执行职能、运营人员和采购办公室。',
        '可疑安全事件包括收到可能包含恶意代码的可疑电子邮件。',
        '可疑供应链事件包括将假冒硬件或恶意代码插入组织系统或系统组件。',
        '对联邦机构而言，涉及个人可识别信息的事件被视为泄露。',
        '泄露导致未经授权披露、失去控制、未经授权获取、失陷或类似情况：未经授权的用户访问或可能访问个人可识别信息，或授权用户为授权目的以外的用途访问或可能访问此类信息。',
    ],
}
review = []
for source in compiled['sources']:
    if source['locator'] not in aligned:
        continue
    units = [u for u in source['units'] if u['text'].strip()]
    for unit, chinese in zip(units, aligned[source['locator']], strict=True):
        translations['units'][unit['id']] = {'english_sha256': hashlib.sha256(unit['text'].encode()).hexdigest(),
                                            'reading_translation': chinese}
        review.append({'id': unit['id'], 'locator': source['locator'], 'english': unit['text'], 'reading_translation': chinese})
    if source['locator'] == 'au-6':
        definitions = json.loads(units[-1]['text'])
        for definition, chinese in zip(definitions, aligned['au-6'][-1].splitlines(), strict=True):
            translations['parameters'][definition['id']] = {'original_definition': definition,
                'source_text_sha256': source['canonical_text_sha256'], 'reading_translation': chinese.split('：', 1)[1]}
output = HERE / 'bilingual-units-r3.json'
assert not output.exists()
output.write_text(json.dumps(translations, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
(HERE / 'translation-review-r3.json').write_text(json.dumps({'role': 'Codex source-only AI review, not human/NIST official translation',
    'new_units': review, 'source_only': True, 'plan_terms_distinct': 'incident response plan=事件响应计划; contingency planning=应急规划',
    'no_term_definitions_added': True, 'questions_labels_or_answers_included': False}, ensure_ascii=False, indent=2)+'\n', encoding='utf8')
print(json.dumps({'new_source_units': len(review), 'new_parameters': 3, 'paid_calls': 0}))
