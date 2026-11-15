"""Robust carrier selection analysis.

The declared preference model is ordinal, never a weighted score:

* each criterion has a fixed business direction (min/max) and a fixed
  business reference domain; the domain is not re-derived from the current
  candidate set;
* preferences are coordinate-wise monotone.  A carrier whose performance
  interval is at least as good on every criterion, and separated as strictly
  better on at least one, is necessarily preferred;
* recorded review opinions (``strict`` / ``weak``) constrain that same
  ordinal relation.  ``strict`` asserts a necessary order; ``weak`` allows a
  tie and never creates a strict rank on its own;
* performance intervals are closed *epistemic* bounds, not point estimates.
  An order is asserted only when bounds separate.  Midpoints are never used;
* necessary strict relations are the transitive closure of interval
  dominance and non-contradicted opinions (``a >= b > c`` still implies
  ``a > c``, while a chain of ties never becomes a strict rank).  A possible
  winner is a candidate that no other candidate which can never fail its
  veto necessarily strictly beats, so overlapping intervals stay tied instead
  of being forced apart;
* business vetoes are hard gates.  A definite violation removes a candidate
  regardless of any other strength.  A possible violation (the interval
  straddles the threshold) is kept as a conditional exclusion; it is never
  passed by taking a midpoint;
* conflicting opinions are reported as groups and suspended from inference.
  No opinion is down-weighted.  The group most in need of confirmation is
  flagged first.  An appended meeting answer may settle a *direct*
  contradiction: the answer becomes the latest word, older opinions that
  point the other way are recorded as set aside (not deleted, not
  down-weighted), and the resolution is disclosed.  Answers that themselves
  contradict separated interval evidence are rejected rather than applied.

The module produces an analysis, not a commitment: it never selects or signs
a carrier.
"""

from itertools import combinations

from app.alternatives import comparable, performance_rows
from app.comparison import canonical_interval
from app.preferences import scoped_preferences
from app.store import finite_number, unique

STRICT = 2
WEAK = 1
MAX_QUESTIONS = 2
DIRECTIONS = {'min', 'max'}
TYPE_ORDER = {'direct_contradiction': 0,
              'preference_against_intervals': 1,
              'cyclic_strict': 2,
              'mixed_cycle': 3}
RESOLVABLE = {'direct_contradiction'}
DATA_CONFLICTS = {'preference_against_intervals', 'mixed_cycle'}


# ---------------------------------------------------------------------------
# input checks


def _criteria(case):
    criteria = unique(case['criteria'], 'id')
    if not criteria:
        raise ValueError('at least one criterion required')
    for identifier, criterion in criteria.items():
        if criterion.get('direction') not in DIRECTIONS:
            raise ValueError('invalid criterion direction: ' + str(identifier))
        try:
            lower, upper = criterion['domain']
        except (KeyError, TypeError, ValueError):
            raise ValueError('criterion domain required: ' + str(identifier))
        finite_number(lower)
        finite_number(upper)
        if lower > upper:
            raise ValueError('invalid criterion domain: ' + str(identifier))
    return criteria


def _lane(case, lane):
    if lane is None:
        lane = case.get('lane')
    if not isinstance(lane, str) or not lane:
        raise ValueError('lane required')
    covered = {name for alternative in case['alternatives']
               for name in alternative['coverage']}
    if lane not in covered:
        raise ValueError('unknown lane')
    return lane


def _answers(case, lane, answers):
    if answers is None:
        return []
    if not isinstance(answers, (list, tuple)):
        raise ValueError('answers must be a list')
    rows = []
    for answer in answers:
        if not isinstance(answer, dict):
            raise ValueError('answer must be an object')
        row = dict(answer)
        for field in ('id', 'better', 'worse'):
            if field not in row:
                raise ValueError('answer requires ' + field)
        row.setdefault('kind', 'strict')
        row.setdefault('scope', 'clarification')
        row.setdefault('lane', lane)
        if row['lane'] != lane:
            raise ValueError('answer lane mismatch: ' + str(row['id']))
        if row['kind'] not in ('strict', 'weak'):
            raise ValueError('unknown preference kind')
        rows.append(row)
    return rows


def _vetoes(case, lane):
    criteria = unique(case['criteria'], 'id')
    indexed = {}
    applied = []
    for veto in case['vetoes']:
        identifier = veto['id']
        if identifier in indexed:
            raise ValueError('duplicate veto id: ' + str(identifier))
        if veto['criterion'] not in criteria:
            raise ValueError('veto references unknown criterion: ' + str(veto['criterion']))
        if criteria[veto['criterion']]['direction'] != 'min':
            raise ValueError('veto requires a minimum-direction criterion: ' + str(identifier))
        finite_number(veto['maximum'])
        indexed[identifier] = dict(veto)
        if veto.get('lane') in (None, lane):
            applied.append(dict(veto))
    return applied


# ---------------------------------------------------------------------------
# performance intervals


def _oriented(values, direction):
    lower, upper = values
    return (lower, upper) if direction == 'max' else (-upper, -lower)


def _performance(case, lane, criteria):
    candidates = sorted(comparable(case, lane))
    rows = performance_rows(case, lane)
    normalized = {}
    incomplete = {}
    outside_domain = []
    for candidate in candidates:
        missing = [identifier for identifier in criteria
                   if (candidate, identifier) not in rows]
        if missing:
            incomplete[candidate] = missing
            continue
        for identifier, criterion in criteria.items():
            raw = rows[(candidate, identifier)]
            try:
                values = list(canonical_interval(raw, criterion))
            except KeyError:
                raise ValueError('invalid observation for ' + candidate + '/' + identifier)
            finite_number(values[0])
            finite_number(values[1])
            if values[0] > values[1]:
                raise ValueError('invalid performance interval')
            low, high = criterion['domain']
            if values[0] < low or values[1] > high:
                outside_domain.append({'alternative': candidate, 'criterion': identifier,
                                       'interval': values, 'domain': [low, high]})
            normalized[(candidate, identifier)] = {
                'values': values,
                'bounds': _oriented(values, criterion['direction']),
            }
    return candidates, normalized, incomplete, outside_domain


def _covers(left, right):
    return all(left[criterion][0] >= right[criterion][1] for criterion in left)


def _strictly(left, right):
    return any(left[criterion][0] > right[criterion][1] for criterion in left)


def _dominance_evidence(left, right):
    return {'kind': 'interval_dominance', 'strength': 'strict',
            'separated_on': [criterion for criterion in left
                             if left[criterion][0] > right[criterion][1]],
            'reason': '每条准则的区间下界都不低于对方上界，且至少一条严格分离'}


def _dominance(candidates, normalized, criteria, incomplete):
    """Sure interval dominance: every bound at least as good, at least one
    strictly separated.  Directions are already applied."""
    found = {}
    for better, worse in combinations(candidates, 2):
        if better in incomplete or worse in incomplete:
            continue
        left = {criterion: normalized[(better, criterion)]['bounds'] for criterion in criteria}
        right = {criterion: normalized[(worse, criterion)]['bounds'] for criterion in criteria}
        if _covers(left, right) and _strictly(left, right):
            found[(better, worse)] = _dominance_evidence(left, right)
        elif _covers(right, left) and _strictly(right, left):
            found[(worse, better)] = _dominance_evidence(right, left)
    return found


# ---------------------------------------------------------------------------
# opinion conflicts


def _reachable(nodes, edges):
    reach = {node: {node} for node in nodes}
    for source, target in edges:
        reach[source].add(target)
    changed = True
    while changed:
        changed = False
        for node in nodes:
            expanded = set()
            for child in reach[node]:
                expanded |= reach.get(child, set())
            if expanded - reach[node]:
                reach[node] |= expanded
                changed = True
    return reach


def _strong_components(nodes, edges):
    reach = _reachable(nodes, edges)
    groups = []
    seen = set()
    for node in nodes:
        if node in seen:
            continue
        group = {other for other in nodes
                 if other == node or (other in reach[node] and node in reach[other])}
        seen |= group
        groups.append(group)
    return groups


def _conflicts(opinions, normalized, criteria, dominance):
    groups = []

    def add(group):
        group.setdefault('needs_confirmation', True)
        for existing in groups:
            if set(existing['preferences']) == set(group['preferences']):
                existing['alternatives'] = sorted(set(existing['alternatives'])
                                                  | set(group['alternatives']))
                existing['data_involved'] = (existing['data_involved']
                                             or group['data_involved'])
                if TYPE_ORDER[group['type']] < TYPE_ORDER[existing['type']]:
                    existing['type'] = group['type']
                    existing['explanation'] = group['explanation']
                return
        groups.append(group)

    pairs = {}
    for identifier, opinion in opinions.items():
        pairs.setdefault(tuple(sorted((opinion['better'], opinion['worse']))), []).append(identifier)

    for (left, right), identifiers in sorted(pairs.items()):
        forward = [identifier for identifier in identifiers
                   if opinions[identifier]['better'] == left]
        backward = [identifier for identifier in identifiers
                    if opinions[identifier]['better'] == right]
        if forward and backward and any(opinions[identifier]['kind'] == 'strict'
                                        for identifier in identifiers):
            add({'type': 'direct_contradiction',
                 'alternatives': [left, right],
                 'preferences': sorted(identifiers),
                 'data_involved': False,
                 'explanation': '意见对同一对承运商给出相反方向，无法同时成立'})

    known = {candidate for candidate, _ in normalized}

    for identifier, opinion in sorted(opinions.items()):
        better, worse = opinion['better'], opinion['worse']
        if better not in known or worse not in known:
            continue
        if (worse, better) in dominance:
            add({'type': 'preference_against_intervals',
                 'alternatives': sorted((better, worse)),
                 'preferences': [identifier],
                 'data_involved': True,
                 'explanation': '意见方向与区间资料在每条准则上的分离方向相反，需确认以哪一方为准'})

    opinion_edges = {(opinion['better'], opinion['worse'])
                     for opinion in opinions.values()}
    shared = set(dominance) | opinion_edges
    shared_nodes = sorted({node for pair in shared for node in pair})
    for members in _strong_components(shared_nodes, shared):
        if len(members) < 2:
            continue
        internal = sorted(identifier for identifier, opinion in opinions.items()
                          if opinion['better'] in members and opinion['worse'] in members)
        if not internal:
            continue
        data_cycle = any(better in members and worse in members
                         for better, worse in dominance)
        strict_opinion = any(opinions[identifier]['kind'] == 'strict'
                             for identifier in internal)
        if not data_cycle and not strict_opinion:
            # a cycle made only of "not worse" opinions is consistent with ties
            continue
        add({'type': 'mixed_cycle' if data_cycle else 'cyclic_strict',
             'alternatives': sorted(members),
             'preferences': internal,
             'data_involved': data_cycle,
             'explanation': ('意见与区间资料合在一起形成严格循环，无法全部保留'
                             if data_cycle else '意见彼此形成严格循环，无法全部保留')})

    groups.sort(key=lambda group: (TYPE_ORDER[group['type']], group['alternatives'],
                                   group['preferences']))
    for index, group in enumerate(groups):
        group['priority'] = index + 1
    return groups


def _resolve_conflicts(conflicts, opinions, answer_ids):
    """Split detected conflicts into active and answer-resolved groups.

    A direct contradiction is settled only by an appended *strict* answer that
    is not itself contradicted by interval evidence.  The latest direction
    wins; old opinions pointing the other way are recorded as set aside, old
    opinions pointing the same way stay in force.  A weak answer cannot
    overturn a strict review opinion.
    """
    tainted = set()
    for group in conflicts:
        if group['type'] in DATA_CONFLICTS:
            tainted |= {identifier for identifier in group['preferences']
                        if identifier in answer_ids}

    active = []
    resolved = []
    set_aside = set()
    for group in conflicts:
        if group['type'] in RESOLVABLE:
            appended = [identifier for identifier in group['preferences']
                        if identifier in answer_ids and identifier not in tainted
                        and opinions[identifier]['kind'] == 'strict']
            directions = {(opinions[identifier]['better'], opinions[identifier]['worse'])
                          for identifier in appended}
            if appended and len(directions) == 1:
                better, worse = next(iter(directions))
                older = [identifier for identifier in group['preferences']
                         if identifier not in appended]
                dropped = sorted(identifier for identifier in older
                                 if (opinions[identifier]['better'],
                                     opinions[identifier]['worse']) == (worse, better))
                kept = sorted(identifier for identifier in older
                              if identifier not in dropped)
                set_aside |= set(dropped)
                resolved.append({**group, 'resolved_by': sorted(appended),
                                 'resolved_direction': [better, worse],
                                 'opinions_set_aside': dropped,
                                 'opinions_still_in_force': kept})
                continue
        active.append(group)
    return active, resolved, set_aside


def _suspension(active, set_aside):
    suspended = set()
    for group in active:
        suspended |= set(group['preferences'])
    suspended -= set_aside
    return suspended


# ---------------------------------------------------------------------------
# necessary relations


def _direct_relations(opinions, dominance, excluded):
    evidence = {}
    strength = {}

    def add(better, worse, descriptor, level):
        evidence.setdefault((better, worse), []).append(descriptor)
        strength[(better, worse)] = max(strength.get((better, worse), 0), level)

    for better, worse in sorted(dominance):
        add(better, worse, dict(dominance[(better, worse)]), STRICT)
    for identifier, opinion in sorted(opinions.items()):
        if identifier in excluded or opinion['better'] == opinion['worse']:
            continue
        add(opinion['better'], opinion['worse'],
            {'kind': 'preference', 'id': identifier,
             'strength': opinion['kind'],
             'scope': opinion.get('scope')},
            STRICT if opinion['kind'] == 'strict' else WEAK)
    return evidence, strength


def _closure(nodes, strength):
    """Transitive closure with ordinal semantics.

    ``WEAK`` means "not worse" (>=) and ``STRICT`` means "better" (>).  A
    chain a>=b>c or a>b>=c still implies a>c, so a composed edge is strict
    when *at least one* of its links is strict.  A chain of only weak links
    implies nothing strict.
    """
    matrix = dict(strength)
    for middle in nodes:
        for source in nodes:
            if source == middle:
                continue
            for target in nodes:
                if target == middle:
                    continue
                first = matrix.get((source, middle), 0)
                second = matrix.get((middle, target), 0)
                if not first or not second:
                    continue
                combined = max(first, second)
                if combined > matrix.get((source, target), 0):
                    matrix[(source, target)] = combined
    return matrix


def _strict_path(start, goal, nodes, strength):
    """Shortest path from start to goal containing at least one strict link.

    Such a path proves a strict transitive relation.  A path of only weak
    opinions proves nothing strict and is therefore not returned.
    """
    queue = [[start, False, [start]]]
    visited = {(start, False)}
    position = 0
    while position < len(queue):
        node, strong, path = queue[position]
        position += 1
        for target in nodes:
            level = strength.get((node, target), 0)
            if not level:
                continue
            strict = strong or level == STRICT
            if target == goal and strict:
                return path + [target]
            state = (target, strict)
            if state not in visited:
                visited.add(state)
                queue.append([target, strict, path + [target]])
    return None


def _necessary(nodes, strength, evidence):
    matrix = _closure(nodes, strength)
    result = []
    for better in nodes:
        for worse in nodes:
            if better == worse or matrix.get((better, worse), 0) < STRICT:
                continue
            direct = [descriptor for descriptor in evidence.get((better, worse), ())
                      if descriptor['kind'] == 'interval_dominance'
                      or descriptor.get('strength') == 'strict']
            if direct:
                proof = direct
            else:
                path = _strict_path(better, worse, nodes, strength)
                if not path:
                    continue
                proof = [{'kind': 'transitive',
                          'reason': '严格性沿传递链保持',
                          'chain': [{'better': source, 'worse': target,
                                     'evidence': evidence[(source, target)]}
                                    for source, target in zip(path, path[1:])]}]
            result.append({'better': better, 'worse': worse, 'evidence': proof})
    return result


# ---------------------------------------------------------------------------
# vetoes and winners


def _veto_state(vetoes, normalized, candidates, criteria):
    checked = []
    definite = set()
    conditional = []
    for veto in vetoes:
        criterion = veto['criterion']
        limit = veto['maximum']
        entries = []
        for candidate in candidates:
            if (candidate, criterion) not in normalized:
                continue
            lower, upper = normalized[(candidate, criterion)]['values']
            if lower > limit:
                status = 'definite_fail'
                definite.add(candidate)
            elif upper > limit:
                status = 'uncertain'
                conditional.append({
                    'veto': veto['id'], 'alternative': candidate,
                    'condition': ('只有确认 ' + criterion + ' 不高于 ' + str(limit)
                                  + ' 才不会触发否决；当前区间 ['
                                  + str(lower) + ', ' + str(upper) + '] 未给出结论'),
                })
            else:
                status = 'pass'
            entries.append({'alternative': candidate, 'interval': [lower, upper],
                            'status': status})
        checked.append({'id': veto['id'], 'criterion': criterion,
                        'unit': criteria[criterion]['unit'],
                        'maximum': limit, 'candidates': entries})
    return checked, definite, conditional


def _winner_state(nodes, necessary, definite, conditional):
    """Possible winners under the declared model.

    A definite veto failure is out, regardless of its other strengths.  An
    uncertain veto is neither passed by taking a midpoint nor treated as a
    failure: the candidate is a possible winner exactly in the completions
    where its vetoeed value stays within the threshold, and is flagged as
    conditional.  A candidate is beaten only by a candidate that is eligible
    with no outstanding condition, because any conditional competitor could
    fail its veto in the completion that favours this candidate.
    """
    conditional_names = {row['alternative'] for row in conditional}
    unconditional = [candidate for candidate in nodes
                     if candidate not in definite and candidate not in conditional_names]
    edges = {(row['better'], row['worse']) for row in necessary}
    beaten = {worse for better, worse in edges
              if better in unconditional and worse in unconditional}
    # an unconditional competitor never fails its veto, so a conditional
    # candidate that such a competitor necessarily beats can never win
    beaten_by_unconditional = {worse for better, worse in edges
                               if better in unconditional}
    winners = [candidate for candidate in unconditional if candidate not in beaten]
    conditional_winners = [candidate for candidate in nodes
                           if candidate in conditional_names
                           and candidate not in definite
                           and candidate not in beaten_by_unconditional]
    ordered = sorted(winners) + [candidate for candidate in conditional_winners
                                 if candidate not in winners]
    return ordered, conditional_winners


# ---------------------------------------------------------------------------
# evaluation


def _evaluate(case, lane, criteria, answers):
    candidates, normalized, incomplete, outside_domain = _performance(case, lane, criteria)
    opinions = {row['id']: row for row in scoped_preferences(case, lane, answers)}
    lane_members = set(comparable(case, lane))
    for identifier, opinion in opinions.items():
        for side in ('better', 'worse'):
            if opinion[side] not in lane_members:
                raise ValueError('opinion ' + identifier
                                 + ' references a carrier outside the lane')
        if opinion['better'] == opinion['worse'] and opinion['kind'] == 'strict':
            raise ValueError('strict self preference')

    dominance = _dominance(candidates, normalized, criteria, incomplete)
    detected = _conflicts(opinions, normalized, criteria, dominance)
    answer_ids = {row['id'] for row in answers}
    active, resolved, set_aside = _resolve_conflicts(detected, opinions, answer_ids)
    suspended = _suspension(active, set_aside)
    excluded = suspended | set_aside
    evidence, strength = _direct_relations(opinions, dominance, excluded)

    nodes = [candidate for candidate in candidates if candidate not in incomplete]
    necessary = _necessary(nodes, strength, evidence)
    vetoes, definite, conditional = _veto_state(_vetoes(case, lane), normalized,
                                                nodes, criteria)
    winners, conditional_winners = _winner_state(nodes, necessary, definite, conditional)
    return {
        'candidates': candidates,
        'normalized': normalized,
        'incomplete': incomplete,
        'outside_domain': outside_domain,
        'opinions': opinions,
        'dominance': dominance,
        'conflicts': active,
        'resolved_conflicts': resolved,
        'suspended': suspended,
        'set_aside': set_aside,
        'necessary': necessary,
        'vetoes': vetoes,
        'definite': definite,
        'conditional': conditional,
        'winners': winners,
        'conditional_winners': conditional_winners,
    }


# ---------------------------------------------------------------------------
# clarifying questions


def _edge_pairs(necessary):
    return {(row['better'], row['worse']) for row in necessary}


def _diff(base, trial, answer_id):
    base_pairs, trial_pairs = _edge_pairs(base['necessary']), _edge_pairs(trial['necessary'])
    rejected = [group for group in trial['conflicts']
                if answer_id in group['preferences']]
    settled = [group for group in trial['resolved_conflicts']
               if answer_id in group['resolved_by']]
    if rejected:
        status = 'rejected_by_conflict'
    elif settled:
        status = 'resolved_contradiction'
    else:
        status = 'applied'
    return {
        'status': status,
        'necessary_edges_added': sorted([better, worse]
                                        for better, worse in trial_pairs - base_pairs),
        'necessary_edges_removed': sorted([better, worse]
                                          for better, worse in base_pairs - trial_pairs),
        'possible_winners_before': list(base['winners']),
        'possible_winners_after': list(trial['winners']),
        'winners_removed': sorted(set(base['winners']) - set(trial['winners'])),
        'winners_added': sorted(set(trial['winners']) - set(base['winners'])),
        'opinions_set_aside': sorted(trial['set_aside'] - base['set_aside']),
        'conflicts_resolved': [
            {'type': group['type'], 'alternatives': list(group['alternatives']),
             'resolved_by': list(group['resolved_by']),
             'opinions_set_aside': list(group['opinions_set_aside'])}
            for group in settled],
        'conflicts_involving_answer': [
            {'type': group['type'], 'preferences': sorted(group['preferences']),
             'alternatives': list(group['alternatives'])}
            for group in rejected],
    }


def _effect(changes):
    return (len(changes['necessary_edges_added'])
            + len(changes['necessary_edges_removed'])
            + len(changes['winners_removed'])
            + len(changes['winners_added']))


def _fresh(base, taken):
    identifier = base
    index = 2
    while identifier in taken:
        identifier = base + '-' + str(index)
        index += 1
    taken.add(identifier)
    return identifier


def _answer_effect(case, lane, criteria, answers, base, better, worse, answer_id):
    trial = _evaluate(case, lane, criteria, answers + [{
        'id': answer_id, 'better': better, 'worse': worse, 'kind': 'strict',
        'lane': lane, 'scope': 'clarification'}])
    return _diff(base, trial, answer_id)


def _conflict_pair(group, opinions):
    """Pick a pair inside a conflict group whose directions actually clash."""
    alternatives = group['alternatives']
    for left, right in combinations(alternatives, 2):
        forward = any(opinions[identifier]['better'] == left
                      and opinions[identifier]['worse'] == right
                      for identifier in group['preferences'])
        backward = any(opinions[identifier]['better'] == right
                       and opinions[identifier]['worse'] == left
                       for identifier in group['preferences'])
        if forward and backward:
            return left, right
    if len(alternatives) == 2:
        return alternatives[0], alternatives[1]
    return None


def _questions(case, lane, criteria, answers, state):
    taken = set(state['opinions'])
    questions = []
    winners = sorted(state['winners'])
    disputed = {tuple(sorted(group['alternatives'])) for group in state['conflicts']}

    def question(question_type, left, right, group=None):
        number = len(questions) + 1
        outcomes = []
        effect = 0
        for better, worse in ((left, right), (right, left)):
            answer_id = _fresh('q' + str(number) + '-' + better + '-over-' + worse, taken)
            changes = _answer_effect(case, lane, criteria, answers, state,
                                     better, worse, answer_id)
            effect += _effect(changes)
            outcomes.append({
                'answer': {'id': answer_id, 'better': better, 'worse': worse,
                           'kind': 'strict', 'lane': lane, 'scope': 'clarification'},
                'changes': changes,
            })
        if not effect:
            return None
        if question_type == 'conflict':
            prompt = ('意见 ' + '/'.join(group['preferences']) + ' 对 ' + left + ' 与 '
                      + right + ' 的方向互相矛盾，无法同时成立。请确认哪一方成立：')
        else:
            prompt = (left + ' 与 ' + right + ' 目前没有被资料或现有意见区分开。'
                      '请确认是否有一方严格更优：')
        return {'id': 'q' + str(number), 'type': question_type,
                'alternatives': [left, right],
                'preferences': [] if group is None else list(group['preferences']),
                'prompt': prompt, 'outcomes': outcomes}

    ordered = sorted(state['conflicts'],
                     key=lambda group: (TYPE_ORDER[group['type']],
                                        -len(group['preferences']), group['priority']))
    for group in ordered:
        if len(questions) >= MAX_QUESTIONS:
            break
        pair = _conflict_pair(group, state['opinions'])
        if not pair:
            continue
        item = question('conflict', pair[0], pair[1], group)
        if item:
            questions.append(item)

    ranked = []
    for left, right in combinations(winners, 2):
        if tuple(sorted((left, right))) in disputed:
            continue
        probe = '__probe__'
        forward = _answer_effect(case, lane, criteria, answers, state,
                                 left, right, probe + ':f')
        backward = _answer_effect(case, lane, criteria, answers, state,
                                  right, left, probe + ':b')
        winner_effect = (len(forward['winners_removed']) + len(forward['winners_added'])
                         + len(backward['winners_removed']) + len(backward['winners_added']))
        edge_effect = _effect(forward) + _effect(backward)
        if winner_effect or edge_effect:
            ranked.append((winner_effect, edge_effect, left, right))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2], item[3]))
    for _, _, left, right in ranked:
        if len(questions) >= MAX_QUESTIONS:
            break
        item = question('comparison', left, right)
        if item:
            questions.append(item)
    return questions


# ---------------------------------------------------------------------------
# public entry point


def _winner_basis(state):
    edges = state['necessary']
    conditional = {row['alternative'] for row in state['conditional']}
    basis = []
    for winner in state['winners']:
        basis.append({
            'alternative': winner,
            'beats': sorted({row['worse'] for row in edges if row['better'] == winner}),
            'necessary_losses': sorted({row['better'] for row in edges if row['worse'] == winner}),
            'veto': 'conditional' if winner in conditional else 'pass',
        })
    return basis


def _reversible(state, questions):
    conditions = []
    for question in questions:
        for outcome in question['outcomes']:
            changes = outcome['changes']
            conditions.append({
                'question': question['id'],
                'if_answered': {'better': outcome['answer']['better'],
                                'worse': outcome['answer']['worse'],
                                'kind': outcome['answer']['kind']},
                'status': changes['status'],
                'winners_after': list(changes['possible_winners_after']),
                'edges_added': list(changes['necessary_edges_added']),
                'edges_removed': list(changes['necessary_edges_removed']),
                'opinions_set_aside': list(changes['opinions_set_aside']),
            })
    return conditions


def analyze(case, lane=None, answers=()):
    lane = _lane(case, lane)
    criteria = _criteria(case)
    answers = _answers(case, lane, answers)
    state = _evaluate(case, lane, criteria, answers)
    questions = _questions(case, lane, criteria, answers, state)
    necessary = state['necessary']
    winners = state['winners']

    edges = _edge_pairs(necessary)
    undecided = sorted([left, right]
                       for left, right in combinations(sorted(winners), 2)
                       if (left, right) not in edges and (right, left) not in edges)
    urgent = state['conflicts'][0] if state['conflicts'] else None

    still_missing = {
        'incomplete_observations': [
            {'alternative': candidate, 'criteria': list(missing)}
            for candidate, missing in sorted(state['incomplete'].items())],
        'uncertain_vetoes': [{'veto': row['veto'], 'alternative': row['alternative'],
                              'condition': row['condition']}
                             for row in state['conditional']],
        'unresolved_conflicts': [
            {'type': group['type'], 'alternatives': list(group['alternatives']),
             'preferences': list(group['preferences'])}
            for group in state['conflicts']],
        'undecided_comparisons': undecided,
        'data_quality': state['outside_domain'],
    }

    diagnostics = {
        'lane': lane,
        'preference_model': {
            'family': '序数型、逐准则单调偏好；不使用权重、评分或归一化',
            'orders': '只在区间分离或意见明确时断言必要顺序；重叠区间保持并列，不取中点',
            'scale': '各准则使用固定业务参照域，不随当前候选集重新标定',
            'strict_opinion': '断言必要严格顺序',
            'weak_opinion': '不劣意见允许并列，不单独产生严格名次',
            'transitivity': '严格性沿传递链保持；仅有不劣关系的链条不产生严格名次',
            'vetoes': '业务硬门槛，不被其他优点补偿；可能越线保留为条件排除',
            'conflicts': '矛盾意见整组暂停推断，不降低任何一方权重；会后答案可解决直接矛盾并披露被搁置的旧意见',
        },
        'criteria': [{'id': identifier, 'direction': criterion['direction'],
                      'unit': criterion['unit'], 'domain': criterion['domain']}
                     for identifier, criterion in criteria.items()],
        'comparable': state['candidates'],
        'incomplete_data': state['incomplete'],
        'observations_outside_domain': state['outside_domain'],
        'preferences_used': sorted(identifier for identifier in state['opinions']
                                   if identifier not in state['suspended']
                                   and identifier not in state['set_aside']),
        'preferences_suspended': sorted(state['suspended']),
        'preferences_set_aside': sorted(state['set_aside']),
        'resolved_conflicts': state['resolved_conflicts'],
        'vetoes': state['vetoes'],
        'definitely_excluded': sorted(state['definite']),
        'conditional_exclusions': state['conditional'],
        'conditional_winners': state['conditional_winners'],
        'possible_winners_basis': _winner_basis(state),
        'most_urgent_conflict': None if urgent is None else {
            'type': urgent['type'],
            'alternatives': list(urgent['alternatives']),
            'preferences': list(urgent['preferences']),
            'explanation': urgent['explanation'],
            'priority_rule': '直接矛盾优先，其次意见与区间冲突，再其次循环；同类按涉及意见数排序',
        },
        'reversible_conditions': _reversible(state, questions),
        'still_missing': still_missing,
        'limitations': [
            '结论只相对于声明模型与已记录意见成立，不构成客观名次',
            '区间重叠处保留并列，不插值，也不把区间当确定事实',
            '未表达的服务质量等因素不在证据内，无法据此推断其优劣',
            '否决阈值与区间出自业务资料，分析不替代业务复核',
            '本分析不自动签约，也不选择承运商',
        ],
        'not_a_commitment': True,
    }

    return {
        'snapshot': case['_snapshot'],
        'necessary_edges': necessary,
        'possible_winners': list(winners),
        'conflicts': state['conflicts'],
        'questions': questions,
        'diagnostics': diagnostics,
    }
