"""Read native personnel/skill relations, using the F06 observation mechanism."""
def inspect(frepple):
    from freppledb.common.models import Parameter
    def ref(value):return value.name if value else None
    resources=[];skills=[];loads=[]
    for r in frepple.resources():
        resources.append({'name':r.name,'owner':ref(r.owner),'maximum':str(r.maximum),
            'available':ref(r.available),'maximum_calendar':ref(r.maximum_calendar)})
        for s in r.resourceskills:
            skills.append({'resource':r.name,'skill':s.skill.name,'priority':s.priority,
                'effective_start':s.effective_start.isoformat(),'effective_end':s.effective_end.isoformat()})
    for o in frepple.operations():
        if o.hidden:continue
        for l in o.loads:
            loads.append({'operation':o.name,'resource':l.resource.name,'quantity':str(l.quantity),
                'skill':ref(l.skill),'search':l.search})
    return {'resources':resources,'resource_skills':skills,'operation_loads':loads}
