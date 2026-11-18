import copy

GAPS = {'ACTUAL_PURCHASE_STATUS': '尚未接入实际采购承诺与收货状态，不能据此认定供应商违约。',
        'OTHER_CONSTRAINTS': '订单约束可能不完整，不能证明已找出所有限制或唯一原因。',
        'CONSTRAINTS_UNAVAILABLE': '当前无法完整读取订单约束证据，不能确认具体延期原因。',
        'NO_CONSTRAINT_RECORDED': '当前没有返回明确订单约束；这不表示不存在限制，无法确认具体原因。',
        'NO_UNIQUE_CAUSE': '关联任务、资源和物料不是延期因果证明；本次诊断不是现实工厂根因认定。',
        'UNSUPPORTED_PRIORITY': '当前未开放已有订单优先级修改；不会用修改交期冒充，也未创建草案或运行计算。',
        'READ_ONLY': '本轮为只读调查，未修改、审批或发布生产计划。'}
KINDS = {'DELIVERY': {'DELIVERY'}, 'REPORTED_CONSTRAINT': {'ENGINE_CONSTRAINT'},
         'RELATED_ONLY': {'RELATION', 'OPERATION', 'PURCHASE', 'RESOURCE_RELATION', 'MATERIAL_RELATION'}}


class ClaimError(ValueError):
    pass


def validate_answer(answer, ledger):
    if set(answer) != {'claims', 'gaps'} or not isinstance(answer['claims'], list) or not isinstance(answer['gaps'], list):
        raise ClaimError('INVALID_DIAGNOSIS_ANSWER')
    answer=copy.deepcopy(answer)
    known = {e['id']: e for e in ledger}
    for claim in answer['claims']:
        if set(claim) != {'kind', 'evidence_ids'} or claim['kind'] not in KINDS or not isinstance(claim['evidence_ids'], list) or not claim['evidence_ids']:
            raise ClaimError('INVALID_EVIDENCE_CLAIM')
        canonical=[]
        for ref in claim['evidence_ids']:
            if ref not in known:
                matches=[e for e in ledger if e['source'].get('evidence_id')==ref and e['kind'] in KINDS[claim['kind']]]
                if matches:
                    ref=next((e['id'] for e in matches if e['kind']!='RELATION'),matches[0]['id'])
            if ref not in known or known[ref]['kind'] not in KINDS[claim['kind']]:
                raise ClaimError('UNSUPPORTED_CAUSAL_CLAIM: 证据不足以支持该类声明。')
            canonical.append(ref)
        claim['evidence_ids']=list(dict.fromkeys(canonical))
    if any(g not in GAPS for g in answer['gaps']):
        raise ClaimError('UNKNOWN_DIAGNOSIS_GAP')
    if any(e['kind']=='ENGINE_CONSTRAINT' for e in ledger) and any(g in {'NO_CONSTRAINT_RECORDED','CONSTRAINTS_UNAVAILABLE'} for g in answer['gaps']):
        raise ClaimError('CONTRADICTORY_GAP: 已查到订单约束，不能声称没有约束记录或约束不可读。')
    return copy.deepcopy(answer)


def render_answer(answer, ledger, bound, coverage):
    answer = validate_answer(answer, ledger)
    facts = {e['id']: e for e in ledger}
    # Essential known findings cannot be hidden by a model selecting fewer claims.
    chosen = list(dict.fromkeys([r for c in answer['claims'] for r in c['evidence_ids']]
                  + [e['id'] for e in ledger if e['kind'] in {'DELIVERY', 'ENGINE_CONSTRAINT'}]))
    gaps = list(dict.fromkeys(answer['gaps'] + ['OTHER_CONSTRAINTS', 'NO_UNIQUE_CAUSE', 'READ_ONLY']))
    if not any(e['kind'] == 'ENGINE_CONSTRAINT' for e in ledger):
        gaps.insert(0, 'CONSTRAINTS_UNAVAILABLE' if coverage.get('get_order_constraints') in {'UNAVAILABLE','ERROR','PARTIAL'} else 'NO_CONSTRAINT_RECORDED')
    if any(e['kind'] == 'ENGINE_CONSTRAINT' and 'lead time' in str(e['data'].get('name')) for e in ledger):
        gaps.append('ACTUAL_PURCHASE_STATUS')
    text = f"已调查订单 {bound['order_id']}，固定运行 {bound['scope']['run_id']}。\n"
    for ref in chosen:
        e = facts[ref]
        text += '\n' + e['text'] + f" 来源 {e['source'].get('artifact')}#{e['source'].get('locator')}。"
    boundary='\n\n结论边界：\n' + '\n'.join(GAPS[g] for g in dict.fromkeys(gaps))
    text += boundary
    selection=bound['selection'];selected_ref=selection.get('id','').split(':')[-1] if selection.get('type')=='operation' else None
    summary=f"已调查订单 {bound['order_id']}。\n"
    for ref in chosen:
        e=facts[ref]
        focused=(selected_ref and (e.get('action',{}).get('id')==selection.get('id') or str(e['data'].get('operationplan'))==selected_ref))
        if e['kind'] in {'DELIVERY','ENGINE_CONSTRAINT'} or focused:
            summary+='\n'+e['text']
    summary+=boundary
    return {'text': text, 'summary_text':summary, 'claims': answer['claims'], 'gaps': list(dict.fromkeys(gaps)),
            'evidence_ids': chosen, 'actions': list({(facts[r]['action']['type'],facts[r]['action']['id']): facts[r]['action'] for r in chosen if facts[r].get('action')}.values())}
