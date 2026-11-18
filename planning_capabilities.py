"""Explicit shared P3/service certification boundaries; never a profile composer."""
import copy,json
from pathlib import Path

CONTRACT=json.loads((Path(__file__).with_name('planning-capabilities.json')).read_text('utf8'))

def profile_id(data):return data.get('foundation_projection',{}).get('schema_revision') or data.get('rules',{}).get('manufacturing_profile') or 'ordinary-routing-v1'

def profiles_for(area,key):return set(CONTRACT.get(area,{}).get(key,{}).get('profiles',[]))

def supports(profile,area,key):return profile in CONTRACT['profiles'] and profile in profiles_for(area,key)

def profile_capabilities(profile):
    value={'schema_version':CONTRACT['schema_version'],'profile':profile,'known_profile':profile in CONTRACT['profiles']}
    for area in ['analyses','actions']:
        value[area]={key:{'status':'AVAILABLE_WITH_EVIDENCE' if supports(profile,area,key) else 'UNSUPPORTED',
            'reason':item['reason'],'evidence_required':copy.deepcopy(item['evidence_required'])}for key,item in CONTRACT[area].items()}
    return value


def supports_input(data,area,key):
    if area=='actions' and key=='plan_editor' and 'foundation_projection' in data:
        from p22.foundation_editor import contract
        return contract(data) is not None
    return supports(profile_id(data),area,key)


def input_capabilities(data):
    """Describe the same input-specific Editor gate used by discovery/Owner.

    The static profile table alone cannot certify a Foundation research scope.
    Its original Editor contract remains authoritative; other capabilities stay
    at their explicit profile certification boundary.
    """
    value=profile_capabilities(profile_id(data))
    value['actions']['plan_editor']['status']=(
        'AVAILABLE_WITH_EVIDENCE' if supports_input(data,'actions','plan_editor') else 'UNSUPPORTED')
    return value
