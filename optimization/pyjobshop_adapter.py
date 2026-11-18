"""A real PyJobShop comparison model, explicitly short of APS certification.

Task starts in this model are PROCESSING starts (native ``setup_end``), not
native setup starts. PyJobShop's setup times are gaps between machine tasks:
they are not working-calendar intervals and do not occupy the named person.
Material reservoirs and delivery pegging remain outside this comparison.
"""
import math
import time
from importlib.metadata import version


def _breaks(windows, horizon):
    """Complement a mode's qualified machine/person calendar in the horizon."""
    merged = []
    for start, end in sorted(windows):
        start, end = max(0, int(start)), min(horizon, int(end))
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else:
            merged.append((start, end))
    blocked, cursor = [], 0
    for start, end in merged:
        if cursor < start:
            blocked.append((cursor, start))
        cursor = end
    if cursor < horizon:
        blocked.append((cursor, horizon))
    return blocked


def solve_pyjobshop(problem, budget_seconds=30):
    """Solve the finite scheduling comparison with PyJobShop/OR-Tools.

    Every leaf and routing precedence is represented. Fixed tasks retain their
    processing dates, machine and named person; fixed setup envelopes reserve
    both capacities separately. Variable task completion is capped at its own
    baseline end, a conservative scheduling restriction, NOT a proof of
    unchanged customer delivery. Initial machine setups use mandatory zero
    duration anchor tasks so the library's setup objective includes them.

    ``model_status`` is the upstream solver status. All results, including
    OPTIMAL, have certification=RELAXATION_ONLY and native_adopted=False.
    The function performs no I/O, native setter calls or Worker submissions.
    """
    if (isinstance(budget_seconds, bool) or
            not isinstance(budget_seconds, (int, float)) or
            not math.isfinite(budget_seconds) or budget_seconds <= 0):
        raise ValueError('PYJOBSHOP_BUDGET: positive finite seconds required')
    from pyjobshop import Model, Solution
    from pyjobshop.Solution import ScheduledTask

    started = time.monotonic()
    jobs = problem['jobs']
    horizon = int(problem['horizon'])
    if not jobs or horizon <= 0 or len({j['id'] for j in jobs}) != len(jobs):
        raise ValueError('PYJOBSHOP_PROBLEM: unique nonempty jobs and horizon required')
    model = Model()
    machine_names = sorted({m['machine'] for j in jobs for m in j['modes']})
    people_names = sorted({m['person'] for j in jobs for m in j['modes']})
    machines = {name: model.add_machine(name='machine:' + name) for name in machine_names}
    capacity = {name: model.add_renewable(1, name='capacity:' + name) for name in machine_names}
    people = {name: model.add_renewable(1, name='person:' + name) for name in people_names}
    tasks, task_index, groups, mode_mapping = {}, {}, {}, {}
    eligible = {name: [] for name in machine_names}
    fixed_reservations = 0
    hint_rows = []

    for j in jobs:
        jid = j['id']
        if j['processing_seconds'] <= 0 or int(j['processing_seconds']) != j['processing_seconds']:
            raise ValueError('PYJOBSHOP_DURATION: positive integer processing required')
        owner = j.get('owner') or jid
        if owner not in groups:
            groups[owner] = model.add_job(name=owner)
        kwargs = dict(allow_breaks=True, latest_end=min(horizon, int(j['end'])), name=jid)
        if j['fixed']:
            kwargs.update(earliest_start=int(j['setup_end']), latest_start=int(j['setup_end']),
                          earliest_end=int(j['end']))
        task_index[jid] = len(model.tasks)
        task = tasks[jid] = model.add_task(groups[owner], **kwargs)
        modes = [m for m in j['modes'] if not j['fixed'] or
                 (m['machine'] == j['machine'] and m['person'] == j['person'])]
        if not modes:
            raise ValueError('PYJOBSHOP_MODES: fixed assignment is not eligible')
        for idx, mode in enumerate(modes):
            if mode['person'] != j['person']:
                raise ValueError('PYJOBSHOP_PERSON: named personnel substitution is not allowed')
            name, person = mode['machine'], mode['person']
            calendar = model.add_machine(_breaks(mode['windows'], horizon),
                                         name=f'calendar:{jid}:{idx}')
            mode_idx = len(model.modes)
            model.add_mode(task, [machines[name], capacity[name], people[person], calendar],
                           int(j['processing_seconds']), demands=[0, 1, 1, 0])
            mode_mapping[mode_idx] = (name, person)
            if name == j['machine'] and person == j['person']:
                hint_rows.append((task_index[jid], mode_idx, int(j['setup_end']), int(j['end']),
                                  int(j['end'] - j['setup_end'] - j['processing_seconds'])))
            if jid not in eligible[name]:
                eligible[name].append(jid)
        if j['fixed'] and j['start'] < j['setup_end']:
            # Reserve the complete immutable setup envelope, including any
            # off-shift portion; the production interval starts at setup_end.
            reservation = model.add_task(earliest_start=int(j['start']), latest_start=int(j['start']),
                                         earliest_end=int(j['setup_end']), latest_end=int(j['setup_end']),
                                         name='fixed-setup:' + jid)
            reservation_idx, reservation_mode = len(model.tasks)-1, len(model.modes)
            model.add_mode(reservation, [capacity[j['machine']], people[j['person']]],
                           int(j['setup_end'] - j['start']), demands=[1, 1])
            hint_rows.append((reservation_idx, reservation_mode, int(j['start']), int(j['setup_end']), 0))
            fixed_reservations += 1

    for before, after in problem.get('precedences', []):
        if before not in tasks or after not in tasks:
            raise ValueError('PYJOBSHOP_PRECEDENCE: missing source task')
        model.add_end_before_start(tasks[before], tasks[after])

    transitions = {}
    for edge in problem.get('setup_transitions', []):
        key = edge['from_state'], edge['to_state']
        duration = edge['duration_seconds']
        if key in transitions or int(duration) != duration or duration < 0:
            raise ValueError('PYJOBSHOP_SETUP: unique nonnegative integer transitions required')
        transitions[key] = int(duration)
    source = {j['id']: j for j in jobs}
    setup_edges, initial_anchors = 0, 0
    for name in machine_names:
        state_jobs = [jid for jid in eligible[name] if source[jid].get('state') is not None]
        if not state_jobs:
            continue
        if len(state_jobs) != len(eligible[name]) or name not in problem.get('initial_setups', {}):
            raise ValueError('PYJOBSHOP_SETUP: complete machine states and initial setup required')
        initial = model.add_task(earliest_start=0, latest_start=0, earliest_end=0,
                                 latest_end=0, name='initial:' + name)
        initial_idx, initial_mode = len(model.tasks)-1, len(model.modes)
        model.add_mode(initial, machines[name], 0)
        hint_rows.append((initial_idx, initial_mode, 0, 0, 0))
        initial_anchors += 1
        origins = [(initial, problem['initial_setups'][name], None)] + [
            (tasks[jid], source[jid]['state'], jid) for jid in state_jobs]
        for task1, state1, id1 in origins:
            for id2 in state_jobs:
                if id1 == id2:
                    continue
                key = state1, source[id2]['state']
                if key not in transitions:
                    raise ValueError('PYJOBSHOP_SETUP: missing transition ' + repr(key))
                model.add_setup_time(machines[name], task1, tasks[id2], transitions[key])
                setup_edges += 1

    # Upstream requires at least one setup record when that objective is on.
    # A model with no setup-bearing machines has the constant-zero objective.
    model.set_objective(weight_makespan=0, weight_total_setup_time=int(setup_edges > 0))
    # A public initial_solution is a hint, never a replacement returned as a
    # solver result. The actual incumbent/status still comes from OR-Tools.
    hints = [None] * len(model.tasks)
    for idx, mode_idx, start, end, breaks in hint_rows:
        if breaks < 0 or hints[idx] is not None:
            raise ValueError('PYJOBSHOP_BASELINE: unique valid baseline mode required')
        hints[idx] = ScheduledTask(mode_idx, model.modes[mode_idx].resources,
                                   start, end, breaks=breaks)
    if any(row is None for row in hints):
        raise ValueError('PYJOBSHOP_BASELINE: baseline mode not represented')
    initial_solution = Solution(model.data(), hints)
    build_seconds = time.monotonic() - started
    result = model.solve(solver='ortools', time_limit=float(budget_seconds),
                         display=False, num_workers=1, random_seed=0,
                         initial_solution=initial_solution)
    assignments = []
    for j in jobs:
        if not result.best.tasks:
            break
        row = result.best.tasks[task_index[j['id']]]
        machine, person = mode_mapping[row.mode]
        assignments.append(dict(id=j['id'], native_ref=j.get('native_ref'), machine=machine,
                                person=person, start=row.start, end=row.end, processing=row.processing,
                                breaks=row.breaks, idle=row.idle, mode=row.mode,
                                fixed=bool(j['fixed']), baseline_setup_start=j['start'],
                                baseline_setup_end=j['setup_end']))

    # These gaps are explicit even if the abstract scheduling model is OPTIMAL.
    gaps = list(problem.get('coverage', {}).get('gaps', []))
    gaps.extend(['MATERIAL_RESERVOIR_UNMAPPED', 'DELIVERY_PEGGING_UNMAPPED',
                 'VARIABLE_SETUP_RESOURCE_OCCUPANCY_UNMAPPED',
                 'SETUP_WORKING_CALENDAR_UNMAPPED', 'NATIVE_READBACK_NOT_EXECUTED'])
    return dict(
        revision='aps-pyjobshop-comparison-v1', algorithm='pyjobshop-ortools',
        versions=dict(pyjobshop=version('pyjobshop'), ortools=version('ortools')),
        problem_sha256=problem.get('problem_sha256'), model_status=result.status.name,
        objective=float(result.objective) if math.isfinite(result.objective) else None,
        lower_bound=float(result.lower_bound) if math.isfinite(result.lower_bound) else None,
        runtime=float(result.runtime), build_seconds=build_seconds,
        wall_seconds=time.monotonic() - started, budget_seconds=float(budget_seconds),
        objective_name='total_elapsed_sequence_setup_seconds_including_initial_states',
        assignment_start_semantics='PROCESSING_START_NOT_NATIVE_SETUP_START',
        assignments=assignments, certification='RELAXATION_ONLY', native_adopted=False,
        execution_authorized=False,
        coverage=dict(gaps=list(dict.fromkeys(gaps)),
                      limits=list(problem.get('coverage', {}).get('limits', [])) + [
                          'Processing calendars use exact eligible mode-window intersections; wall envelopes retain capacity across breaks.',
                          'Setup is an elapsed separation, can cross breaks, and does not occupy variable setup machine/person capacity.',
                          'Every job end is capped at its baseline end; this is not an inventory/pegging proof of unchanged deliveries.',
                          'Objective and lower bound apply only to this comparison model, not to the full APS enterprise problem.',
                          'Fixed setup reservations preserve observed occupancy; alternative predecessor setup feasibility still requires native replay.'],
                      modeled_jobs=len(jobs), fixed_jobs=sum(bool(j['fixed']) for j in jobs),
                      modeled_precedences=len(problem.get('precedences', [])),
                      material_events_seen=len(problem.get('buffer_events', [])),
                      material_events_modeled=0, deliveries_seen=len(problem.get('deliveries', [])),
                      model_tasks=len(model.tasks), model_modes=len(model.modes),
                      named_persons=len(people), physical_machines=len(machines),
                      initial_setup_anchors=initial_anchors, setup_edges=setup_edges,
                      fixed_setup_reservations=fixed_reservations))
