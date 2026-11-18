"""Bounded related-neighborhood search using the project's CP-SAT repairer.

The source plan is the construction incumbent. Every candidate is checked by the
finite-model validator before acceptance. This module neither runs an APS Worker
nor certifies enterprise feasibility or a global optimum.
"""
from collections import defaultdict, deque
from copy import deepcopy
from decimal import Decimal
import math
import random
from time import monotonic


ASSIGNMENT_FIELDS = ('id', 'start', 'setup_end', 'end', 'machine', 'person')


def _baseline(problem):
    return [{k: j[k] for k in ASSIGNMENT_FIELDS} for j in problem['jobs']]


def _signature(assignments):
    return tuple(sorted(tuple(a[k] for k in ASSIGNMENT_FIELDS) for a in assignments))


def _score(check):
    score = tuple(check[k] for k in ('setup_seconds', 'changed_jobs', 'shift_seconds'))
    if any(not math.isfinite(float(x)) or x < 0 for x in score):
        raise ValueError('OPT_SEARCH_INVALID_SCORE')
    return score


def _dependencies(problem):
    """Related closure includes routing and same-item production/consumption.

    A shared raw-material input does not join unrelated consumers. A producer
    with several same-buffer consumers joins all of them: preserving an old
    allocation is not required by the finite reservoir model.
    """
    graph = defaultdict(set)
    for before, after in problem.get('precedences', []):
        graph[before].add(after)
        graph[after].add(before)
    buffers = defaultdict(lambda: [set(), set()])
    for event in problem.get('buffer_events', []):
        if event.get('job') is not None:
            q = Decimal(event['quantity'])
            if q:
                buffers[event['buffer']][0 if q > 0 else 1].add(event['job'])
    for producers, consumers in buffers.values():
        for producer in producers:
            for consumer in consumers:
                if producer != consumer:
                    graph[producer].add(consumer)
                    graph[consumer].add(producer)
    return graph


def neighborhoods(problem, incumbent=None, seed=1):
    """Return multi-job bottleneck, resource and family closures.

    Increasing resource-block widths provide the variable neighborhoods. The
    full eligible set is the final repair neighborhood; source-fixed jobs are
    never made free, even when dependency traversal passes through them.
    """
    jobs = {j['id']: j for j in problem['jobs']}
    movable = {jid for jid, j in jobs.items() if not j['fixed']}
    current = {a['id']: a for a in (incumbent or _baseline(problem))}
    graph = _dependencies(problem)
    rng = random.Random(seed)
    groups = []
    seen = set()

    def add(kind, selected, priority):
        reached = set(selected)
        pending = list(selected)
        while pending:
            for linked in graph[pending.pop()] - reached:
                reached.add(linked)
                pending.append(linked)
        free = reached & movable
        key = frozenset(free)
        if free and key not in seen:
            seen.add(key)
            setup_anchor = any(jobs[jid].get('state') is not None for jid in selected)
            groups.append(dict(kind=kind, free_ids=sorted(free),
                               priority=(0 if setup_anchor else 1,) + priority,
                               tie_break=rng.random()))

    resources = defaultdict(list)
    families = defaultdict(list)
    for jid, job in jobs.items():
        if jid in movable:
            a = current[jid]
            resources['machine:' + a['machine']].append(jid)
            resources['person:' + a['person']].append(jid)
            if job.get('state') is not None:
                families[job['state']].append(jid)
    for resource, ids in sorted(resources.items()):
        ids.sort(key=lambda jid: (current[jid]['start'], current[jid]['end'], jid))
        widths = sorted({min(3, len(ids)), min(6, len(ids)), len(ids)})
        for width in widths:
            windows = [ids[i:i + width] for i in range(len(ids) - width + 1)]
            windows.sort(key=lambda group: -sum(max(0, current[j]['setup_end'] - current[j]['start'])
                                               for j in group))
            for block in windows:
                penalty = sum(max(0, current[j]['setup_end'] - current[j]['start']) for j in block)
                add('resource_block:' + resource, block, (width, -penalty))
    for family, ids in sorted(families.items()):
        add('setup_family:' + family, ids, (len(ids), 0))
    groups.sort(key=lambda g: (g['priority'], g['tie_break']))
    # A broad repair remains reachable even if all local closures are large.
    key = frozenset(movable)
    if movable and key not in seen:
        groups.append(dict(kind='all_movable', free_ids=sorted(movable)))
    for group in groups:
        group.pop('priority', None)
        group.pop('tie_break', None)
    return groups


def _contract(problem, assignments, hint, free_ids):
    """Protect the complete-output and fixed-to-hint repair contract."""
    expected = {j['id']: j for j in problem['jobs']}
    if not isinstance(assignments, list) or len(assignments) != len(expected):
        return 'INCOMPLETE_ASSIGNMENTS'
    try:
        actual = {a['id']: a for a in assignments}
        if set(actual) != set(expected):
            return 'ASSIGNMENT_IDS'
        old = {a['id']: a for a in hint}
        for jid, job in expected.items():
            for field in ASSIGNMENT_FIELDS:
                actual[jid][field]
            locked = job if job['fixed'] else old[jid]
            if (job['fixed'] or jid not in free_ids) and any(
                    actual[jid][k] != locked[k] for k in ASSIGNMENT_FIELDS):
                return 'REPAIR_CHANGED_FROZEN_JOB'
    except (KeyError, TypeError):
        return 'MALFORMED_ASSIGNMENT'
    return None


def _shake(problem, incumbent, free_ids, seed):
    """Rotate starts of related free jobs as a CP hint, never as a candidate.

    The hint may violate calendars/materials. Only the CP repair output, checked
    independently, can enter the search state. Fixed and nonfree rows are copied
    unchanged because they become hard equalities in the repair model.
    """
    hint = deepcopy(incumbent)
    by_id = {a['id']: a for a in hint}
    jobs = {j['id']: j for j in problem['jobs']}
    selected = sorted(jid for jid in free_ids if not jobs[jid]['fixed'])
    setup = [jid for jid in selected if jobs[jid].get('state') is not None]
    rng = random.Random(seed)
    selected = setup if len(setup) >= 2 else selected
    rng.shuffle(selected)
    selected = selected[:6]
    if len(selected) >= 2:
        starts = [by_id[jid]['start'] for jid in selected]
        targets = starts[1:] + starts[:1]
        horizon = problem.get('horizon', max(a['end'] for a in hint) + 86400)
        for jid, target in zip(selected, targets):
            row = by_id[jid]
            shift = max(-row['start'], min(target-row['start'], horizon-row['end']))
            for key in ('start', 'setup_end', 'end'):
                row[key] += shift
    return hint


def search(problem, budget_seconds=60, strategy='vns', seed=1):
    """Run VNS, late acceptance or solution tabu with a shared wall budget.

    Lexicographic score minimizes setup, then changed source jobs, then shift.
    A repair bound/OPTIMAL status applies only to that particular neighborhood.
    The global result is always the best model-checked incumbent seen so far.
    """
    if strategy not in {'vns', 'late_acceptance', 'tabu'}:
        raise ValueError('OPT_SEARCH_STRATEGY')
    budget = float(budget_seconds)
    if not math.isfinite(budget) or budget < 0:
        raise ValueError('OPT_SEARCH_BUDGET')
    started = monotonic()
    deadline = started + budget
    # Deferred imports allow independent orchestration tests while the repairer
    # is under development, without substituting a solver in production.
    from optimization.cp_sat import solve
    from optimization.validate import validate_candidate

    baseline = _baseline(problem)
    initial = validate_candidate(problem, baseline)
    if initial.get('status') != 'PASS':
        raise ValueError('OPT_SEARCH_BASELINE_INVALID')
    current = deepcopy(baseline)
    current_score = best_score = _score(initial)
    best = deepcopy(current)
    best_meta = dict(model_status='SOURCE_INCUMBENT', primary_bound=None,
                     stop_reason='CHECKED_SOURCE_PLAN')
    late = [current_score] * 8
    tabu = deque([_signature(current)], maxlen=8)
    rounds = []
    groups = neighborhoods(problem, current, seed)
    index = 0
    generation = 0
    stop = 'BUDGET_EXHAUSTED'
    if not groups:
        stop = 'NO_MOVABLE_JOBS'
    while groups:
        remaining = deadline - monotonic()
        # Even a tiny repair rebuilds the complete constrained model. Do not
        # spend the final fraction on a call whose setup cannot usefully fit.
        if remaining <= min(0.5, max(0.01, budget * 0.05)):
            break
        group = groups[index % len(groups)]
        free_ids = set(group['free_ids'])
        # Short repairs let broader neighborhoods receive time; this is a
        # per-call share of the single remaining budget, not another budget.
        repair_budget = min(remaining, max(0.05, budget / 6))
        record = dict(round=len(rounds) + 1, strategy=strategy,
                      neighborhood=group['kind'], free_ids=sorted(free_ids),
                      budget_seconds=repair_budget, accepted=False,
                      current_score=list(current_score), best_score=list(best_score))
        shaken = strategy == 'vns' and generation > 0
        repair_hint = _shake(problem, current, free_ids, seed + len(rounds)) if shaken else deepcopy(current)
        record['shaken_hint'] = shaken
        try:
            candidate = solve(problem, budget_seconds=repair_budget, free_ids=free_ids,
                              hint=repair_hint, goal='setup_then_disturbance',
                              seed=seed + len(rounds))
        except (ValueError, RuntimeError, TypeError, KeyError) as error:
            record.update(reason='REPAIR_ERROR', error=str(error), elapsed_seconds=monotonic() - started)
            rounds.append(record)
            stop = 'REPAIR_ERROR'
            break
        record.update(model_status=candidate.get('model_status'),
                      primary_bound=candidate.get('primary_bound'),
                      bound_scope='REPAIR_NEIGHBORHOOD_ONLY',
                      repair_elapsed_seconds=candidate.get('elapsed_seconds'),
                      repair_stop_reason=candidate.get('stop_reason'))
        proposed = candidate.get('assignments')
        failure = _contract(problem, proposed, current, free_ids)
        if failure:
            record.update(reason='REPAIR_CONTRACT_FAILED', issues=[failure])
        else:
            check = validate_candidate(problem, proposed)
            if check.get('status') != 'PASS':
                record.update(reason='MODEL_CHECK_FAILED', issues=check.get('issues', []))
            else:
                score = _score(check)
                signature = _signature(proposed)
                improved = score < best_score
                accepted = score < current_score
                reason = 'IMPROVING' if accepted else 'NOT_IMPROVING'
                if strategy == 'late_acceptance' and score <= late[len(rounds) % len(late)]:
                    accepted, reason = True, 'LATE_ACCEPTANCE'
                if strategy == 'tabu':
                    accepted = signature not in tabu or improved
                    reason = 'ASPIRATION' if improved and signature in tabu else 'TABU_ACCEPT' if accepted else 'TABU'
                record.update(candidate_score=list(score), accepted=accepted, reason=reason,
                              model_check='PASS', improved_best=improved)
                if improved:
                    best, best_score, best_meta = deepcopy(proposed), score, dict(candidate)
                if accepted:
                    current, current_score = deepcopy(proposed), score
                    if strategy == 'tabu':
                        tabu.append(signature)
                    if strategy == 'vns':
                        index = -1
                        generation = 0
                    groups = neighborhoods(problem, current, seed + generation)
        late[len(rounds) % len(late)] = current_score
        record['best_score'] = list(best_score)
        record['elapsed_seconds'] = monotonic() - started
        rounds.append(record)
        index += 1
        if groups and index >= len(groups):
            index = 0
            generation += 1
            # Revisit bounded repairs with another seed; a time-limited local
            # attempt is not evidence that its neighborhood is exhausted.
            groups = neighborhoods(problem, current, seed + generation)
    return dict(strategy=strategy, seed=seed, model_status='FEASIBLE' if best_score!=_score(initial) else 'SOURCE_INCUMBENT',
                best_repair_status=best_meta['model_status'],
                assignments=best, setup_seconds=best_score[0], changed_jobs=best_score[1],
                shift_seconds=best_score[2], primary_bound=best_meta.get('primary_bound'),
                primary_bound_scope='REPAIR_NEIGHBORHOOD_ONLY' if best_meta.get('primary_bound') is not None else None,
                elapsed_seconds=monotonic() - started, budget_seconds=budget,
                stop_reason=stop, rounds=rounds, source_incumbent_check='PASS',
                model_validation='PASS', aps_certified=False, global_optimum_proven=False,
                coverage=deepcopy(problem.get('coverage', {})))
