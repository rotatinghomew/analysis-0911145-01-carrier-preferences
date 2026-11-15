"""Algorithm-design tests for the robust selection analysis.

These tests are independent of the published fixture files: every case is
synthesised in memory so that a fixed case name can never stand in for a
conclusion.  They cover the statistical semantics (interval guarantees,
non-identifiability), veto gates, contradictory opinions, clarification
questions and sensitivity/scale regressions named in docs/verification.md.
"""

import hashlib
import json
import unittest

from app.analysis import analyze

CRITERIA = [
    {'id': 'cost', 'direction': 'min', 'unit': 'cents_per_box', 'domain': [0, 3000]},
    {'id': 'damage', 'direction': 'min', 'unit': 'fraction', 'domain': [0, 0.15]},
]


def case(alternatives, observations, preferences=(), vetoes=(), lane='east',
         criteria=CRITERIA):
    payload = {
        'criteria': criteria,
        'alternatives': [{'id': name, 'coverage': ['east']} for name in alternatives],
        'observations': observations,
        'preferences': list(preferences),
        'lane': lane,
        'vetoes': list(vetoes),
    }
    encoded = json.dumps(payload, sort_keys=True).encode()
    payload['_snapshot'] = hashlib.sha256(encoded).hexdigest()
    return payload


def observation(alternative, criterion, interval, lane='east', unit=None):
    default = {'cost': 'cents_per_box', 'damage': 'fraction'}.get(criterion, 'fraction')
    return {'alternative': alternative, 'criterion': criterion, 'lane': lane,
            'interval': list(interval), 'unit': unit or default, 'support_orders': 10}


def flat(alternatives, cost=100, damage=0.01, lane='east'):
    return [observation(name, 'cost', (cost, cost), lane) for name in alternatives] + \
           [observation(name, 'damage', (damage, damage), lane) for name in alternatives]


def opinion(identifier, better, worse, kind='strict', lane='east'):
    return {'id': identifier, 'better': better, 'worse': worse, 'kind': kind,
            'lane': lane, 'scope': 'committee'}


def edges(result):
    return {(row['better'], row['worse']) for row in result['necessary_edges']}


class IntervalSemanticsTests(unittest.TestCase):
    def test_overlapping_intervals_never_become_a_necessary_order(self):
        result = analyze(case(
            ['a', 'b'],
            [observation('a', 'cost', (100, 200)), observation('a', 'damage', (0.01, 0.02)),
             observation('b', 'cost', (150, 160)), observation('b', 'damage', (0.01, 0.02))]))
        # a is cheaper in the midpoint, b is within a's bounds on every criterion
        self.assertEqual(edges(result), set())
        self.assertEqual(result['possible_winners'], ['a', 'b'])

    def test_touching_intervals_are_not_strictly_separated(self):
        result = analyze(case(
            ['a', 'b'],
            [observation('a', 'cost', (100, 200)), observation('a', 'damage', (0.01, 0.02)),
             observation('b', 'cost', (200, 300)), observation('b', 'damage', (0.01, 0.02))]))
        self.assertEqual(edges(result), set())

    def test_midpoint_choice_cannot_create_an_order(self):
        # a wins on cost only if each interval is replaced by its midpoint, and
        # b wins on damage only under the same substitution.  Bounds overlap on
        # both criteria, so the model must stay silent.
        result = analyze(case(
            ['a', 'b'],
            [observation('a', 'cost', (100, 300)), observation('a', 'damage', (0.02, 0.04)),
             observation('b', 'cost', (200, 400)), observation('b', 'damage', (0.01, 0.03))]))
        self.assertEqual(edges(result), set())
        self.assertEqual(sorted(result['possible_winners']), ['a', 'b'])

    def test_separated_bounds_on_every_criterion_are_a_necessary_order(self):
        result = analyze(case(
            ['a', 'b'],
            [observation('a', 'cost', (100, 150)), observation('a', 'damage', (0.01, 0.02)),
             observation('b', 'cost', (200, 250)), observation('b', 'damage', (0.02, 0.03))]))
        self.assertIn(('a', 'b'), edges(result))
        self.assertEqual(result['possible_winners'], ['a'])

    def test_uncertain_veto_is_conditional_and_not_a_midpoint_pass(self):
        result = analyze(case(
            ['risky', 'safe'],
            [observation('risky', 'cost', (100, 100)), observation('risky', 'damage', (0.02, 0.06)),
             observation('safe', 'cost', (900, 900)), observation('safe', 'damage', (0.01, 0.01))],
            vetoes=[{'id': 'v1', 'criterion': 'damage', 'maximum': 0.04, 'lane': 'east'}]))
        self.assertIn('risky', result['possible_winners'])
        self.assertEqual(result['diagnostics']['conditional_winners'], ['risky'])
        excluded = result['diagnostics']['conditional_exclusions']
        self.assertEqual([row['alternative'] for row in excluded], ['risky'])
        self.assertIn('damage', excluded[0]['condition'])

    def test_definite_veto_is_not_compensated(self):
        result = analyze(case(
            ['cheap_and_bad', 'good'],
            [observation('cheap_and_bad', 'cost', (1, 1)),
             observation('cheap_and_bad', 'damage', (0.10, 0.12)),
             observation('good', 'cost', (2900, 2900)),
             observation('good', 'damage', (0.001, 0.002))],
            vetoes=[{'id': 'v1', 'criterion': 'damage', 'maximum': 0.04, 'lane': 'east'}]))
        self.assertNotIn('cheap_and_bad', result['possible_winners'])
        self.assertEqual(result['diagnostics']['definitely_excluded'], ['cheap_and_bad'])
        self.assertEqual(result['possible_winners'], ['good'])

    def test_unit_conversion_keeps_bounds_and_orientation(self):
        criteria = [{'id': 'cost', 'direction': 'min', 'unit': 'fraction', 'domain': [0, 1]}]
        result = analyze(case(
            ['a', 'b'],
            [observation('a', 'cost', (10, 20), unit='percent'),
             observation('b', 'cost', (30, 40), unit='percent')],
            criteria=criteria))
        self.assertIn(('a', 'b'), edges(result))
        row = result['necessary_edges'][0]
        self.assertEqual(row['evidence'][0]['kind'], 'interval_dominance')

    def test_non_comparable_lane_is_excluded_from_candidates(self):
        payload = case(['a', 'b'], flat(['a', 'b']))
        payload['alternatives'][1]['coverage'] = ['south']
        result = analyze(payload)
        self.assertEqual(result['diagnostics']['comparable'], ['a'])
        self.assertEqual(result['possible_winners'], ['a'])


class VetoGateTests(unittest.TestCase):
    def test_conditional_candidate_beaten_by_unconditional_rival_is_not_a_winner(self):
        result = analyze(case(
            ['x', 'y'],
            [observation('x', 'cost', (100, 100)), observation('x', 'damage', (0.03, 0.05)),
             observation('y', 'cost', (50, 50)), observation('y', 'damage', (0.01, 0.01))],
            vetoes=[{'id': 'v1', 'criterion': 'damage', 'maximum': 0.04, 'lane': 'east'}]))
        self.assertEqual(result['diagnostics']['conditional_exclusions'][0]['alternative'], 'x')
        self.assertEqual(result['possible_winners'], ['y'])
        self.assertEqual(result['diagnostics']['conditional_winners'], [])

    def test_uncertain_veto_candidate_is_not_beaten_by_a_conditional_rival(self):
        result = analyze(case(
            ['x', 'y'],
            [observation('x', 'cost', (100, 100)), observation('x', 'damage', (0.03, 0.05)),
             observation('y', 'cost', (200, 200)), observation('y', 'damage', (0.03, 0.05))],
            vetoes=[{'id': 'v1', 'criterion': 'damage', 'maximum': 0.04, 'lane': 'east'}]))
        # both straddle the threshold; neither is a definite failure
        self.assertEqual(result['diagnostics']['definitely_excluded'], [])
        self.assertEqual(sorted(result['diagnostics']['conditional_winners']), ['x', 'y'])

    def test_veto_on_maximised_criterion_is_rejected(self):
        criteria = CRITERIA + [{'id': 'tracking', 'direction': 'max',
                                'unit': 'fraction', 'domain': [0, 1]}]
        payload = case(['a'], flat(['a']), criteria=criteria,
                       vetoes=[{'id': 'v1', 'criterion': 'tracking',
                                'maximum': 0.5, 'lane': 'east'}])
        with self.assertRaises(ValueError):
            analyze(payload)


class ContradictionTests(unittest.TestCase):
    def test_direct_contradiction_is_one_group_and_nothing_is_downweighted(self):
        payload = case(
            ['a', 'b'], flat(['a', 'b']),
            preferences=[opinion('p1', 'a', 'b'), opinion('p2', 'b', 'a')])
        result = analyze(payload)
        self.assertEqual(len(result['conflicts']), 1)
        self.assertEqual(result['conflicts'][0]['preferences'], ['p1', 'p2'])
        self.assertEqual(result['diagnostics']['preferences_suspended'], ['p1', 'p2'])
        self.assertEqual(result['diagnostics']['preferences_used'], [])
        self.assertEqual(edges(result), set())

    def test_contradiction_is_flagged_as_most_urgent_and_asked_about(self):
        payload = case(
            ['a', 'b', 'c'], flat(['a', 'b', 'c']),
            preferences=[opinion('p1', 'a', 'b'), opinion('p2', 'b', 'a'),
                         opinion('p3', 'c', 'a', kind='weak')])
        result = analyze(payload)
        urgent = result['diagnostics']['most_urgent_conflict']
        self.assertEqual(urgent['type'], 'direct_contradiction')
        self.assertEqual(urgent['preferences'], ['p1', 'p2'])
        self.assertTrue(result['questions'])
        self.assertEqual(result['questions'][0]['type'], 'conflict')
        self.assertEqual(set(result['questions'][0]['preferences']), {'p1', 'p2'})

    def test_cycle_of_strict_opinions_is_reported_as_a_group(self):
        payload = case(
            ['a', 'b', 'c'], flat(['a', 'b', 'c']),
            preferences=[opinion('p1', 'a', 'b'), opinion('p2', 'b', 'c'),
                         opinion('p3', 'c', 'a')])
        result = analyze(payload)
        self.assertEqual(len(result['conflicts']), 1)
        self.assertEqual(result['conflicts'][0]['type'], 'cyclic_strict')
        self.assertEqual(result['conflicts'][0]['preferences'], ['p1', 'p2', 'p3'])
        self.assertEqual(edges(result), set())

    def test_weak_cycle_is_consistent_with_ties(self):
        payload = case(
            ['a', 'b'], flat(['a', 'b']),
            preferences=[opinion('p1', 'a', 'b', 'weak'), opinion('p2', 'b', 'a', 'weak')])
        result = analyze(payload)
        self.assertEqual(result['conflicts'], [])
        self.assertEqual(edges(result), set())
        self.assertEqual(sorted(result['possible_winners']), ['a', 'b'])

    def test_opinion_against_all_bounds_is_reported(self):
        payload = case(
            ['a', 'b'],
            [observation('a', 'cost', (100, 150)), observation('a', 'damage', (0.01, 0.01)),
             observation('b', 'cost', (200, 250)), observation('b', 'damage', (0.02, 0.02))],
            preferences=[opinion('p1', 'b', 'a')])
        result = analyze(payload)
        types = {group['type'] for group in result['conflicts']}
        self.assertIn('preference_against_intervals', types)
        self.assertEqual(result['diagnostics']['preferences_suspended'], ['p1'])

    def test_preference_opinions_still_conflict_on_separated_bounds(self):
        payload = case(
            ['a', 'b'],
            [observation('a', 'cost', (100, 150)), observation('a', 'damage', (0.01, 0.01)),
             observation('b', 'cost', (200, 250)), observation('b', 'damage', (0.02, 0.02))],
            preferences=[opinion('p1', 'b', 'a')])
        result = analyze(payload)
        self.assertNotIn(('b', 'a'), edges(result))


class WeakPreferenceTests(unittest.TestCase):
    def test_weak_opinions_do_not_produce_a_strict_rank(self):
        result = analyze(case(['a', 'b'], flat(['a', 'b']),
                              preferences=[opinion('p1', 'a', 'b', 'weak')]))
        self.assertEqual(edges(result), set())
        self.assertEqual(sorted(result['possible_winners']), ['a', 'b'])

    def test_strict_chain_is_transitive_and_weak_links_do_not_sharpen_it(self):
        payload = case(
            ['a', 'b', 'c'], flat(['a', 'b', 'c']),
            preferences=[opinion('p1', 'a', 'b', 'strict'), opinion('p2', 'b', 'c', 'weak')])
        result = analyze(payload)
        self.assertIn(('a', 'b'), edges(result))
        # a >= b and b >= c gives a >= c, but strictness needs a strict link;
        # a > b supplies it, so a > c is a valid necessary strict relation
        self.assertIn(('a', 'c'), edges(result))
        self.assertNotIn(('b', 'c'), edges(result))
        self.assertEqual(result['possible_winners'], ['a'])

    def test_all_weak_chain_does_not_become_strict(self):
        payload = case(
            ['a', 'b', 'c'], flat(['a', 'b', 'c']),
            preferences=[opinion('p1', 'a', 'b', 'weak'), opinion('p2', 'b', 'c', 'weak')])
        result = analyze(payload)
        self.assertEqual(edges(result), set())
        self.assertEqual(sorted(result['possible_winners']), ['a', 'b', 'c'])


class AnswerTests(unittest.TestCase):
    def test_answers_are_appended_and_old_opinion_ids_cannot_be_overwritten(self):
        with self.assertRaises(ValueError):
            analyze(case(['a', 'b'], flat(['a', 'b']),
                         preferences=[opinion('p1', 'a', 'b')]),
                    answers=[opinion('p1', 'b', 'a')])

    def test_answer_can_settle_a_direct_contradiction_with_disclosure(self):
        payload = case(
            ['a', 'b'], flat(['a', 'b']),
            preferences=[opinion('p1', 'a', 'b'), opinion('p2', 'b', 'a')])
        answer = opinion('a1', 'b', 'a')
        result = analyze(payload, answers=[answer])
        self.assertEqual(result['conflicts'], [])
        resolved = result['diagnostics']['resolved_conflicts']
        self.assertEqual(len(resolved), 1)
        self.assertEqual(resolved[0]['resolved_by'], ['a1'])
        self.assertEqual(resolved[0]['opinions_set_aside'], ['p1'])
        self.assertEqual(resolved[0]['opinions_still_in_force'], ['p2'])
        self.assertEqual(result['diagnostics']['preferences_set_aside'], ['p1'])
        self.assertIn(('b', 'a'), edges(result))

    def test_weak_answer_cannot_settle_a_strict_contradiction(self):
        payload = case(
            ['a', 'b'], flat(['a', 'b']),
            preferences=[opinion('p1', 'a', 'b'), opinion('p2', 'b', 'a')])
        result = analyze(payload, answers=[opinion('a1', 'b', 'a', 'weak')])
        self.assertEqual(len(result['conflicts']), 1)
        self.assertEqual(result['diagnostics']['preferences_set_aside'], [])
        self.assertEqual(edges(result), set())

    def test_answer_against_separated_bounds_is_rejected_not_applied(self):
        payload = case(
            ['a', 'b'],
            [observation('a', 'cost', (100, 150)), observation('a', 'damage', (0.01, 0.01)),
             observation('b', 'cost', (200, 250)), observation('b', 'damage', (0.02, 0.02))])
        result = analyze(payload, answers=[opinion('a1', 'b', 'a')])
        self.assertNotIn(('b', 'a'), edges(result))
        rejected = result['diagnostics']['resolved_conflicts']
        self.assertEqual(rejected, [])
        self.assertIn('a1', result['diagnostics']['preferences_suspended'])
        self.assertIn('preference_against_intervals',
                      {group['type'] for group in result['conflicts']})

    def test_answer_for_a_lane_other_than_the_analysis_is_rejected(self):
        with self.assertRaises(ValueError):
            analyze(case(['a', 'b'], flat(['a', 'b'])),
                    answers=[opinion('a1', 'a', 'b', lane='south')])


class QuestionTests(unittest.TestCase):
    def _result(self):
        return analyze(case(['a', 'b'], flat(['a', 'b'])))

    def test_questions_both_directions_change_a_real_ranking(self):
        result = self._result()
        self.assertTrue(result['questions'])
        question = result['questions'][0]
        self.assertEqual(set(question['alternatives']), {'a', 'b'})
        self.assertEqual(len(question['outcomes']), 2)
        directions = set()
        for outcome in question['outcomes']:
            changes = outcome['changes']
            self.assertEqual(changes['status'], 'applied')
            self.assertTrue(changes['winners_removed'])
            self.assertEqual(len(changes['possible_winners_after']), 1)
            directions.add(outcome['answer']['better'])
        self.assertEqual(directions, {'a', 'b'})

    def test_applying_a_question_answer_reproduces_the_predicted_ranking(self):
        payload = case(['a', 'b'], flat(['a', 'b']))
        original = analyze(payload)
        for outcome in original['questions'][0]['outcomes']:
            answer = outcome['answer']
            follow_up = analyze(payload, answers=[answer])
            self.assertEqual(follow_up['possible_winners'],
                             outcome['changes']['possible_winners_after'])
            added = {tuple(pair) for pair in outcome['changes']['necessary_edges_added']}
            self.assertTrue(added <= edges(follow_up))

    def test_no_question_is_asked_about_a_pair_already_ordered(self):
        payload = case(
            ['a', 'b'],
            [observation('a', 'cost', (100, 150)), observation('a', 'damage', (0.01, 0.01)),
             observation('b', 'cost', (200, 250)), observation('b', 'damage', (0.02, 0.02))])
        result = analyze(payload)
        self.assertEqual(result['questions'], [])

    def test_questions_never_exceed_two(self):
        payload = case(['a', 'b', 'c', 'd'], flat(['a', 'b', 'c', 'd']))
        result = analyze(payload)
        self.assertLessEqual(len(result['questions']), 2)

    def test_reversible_conditions_mirror_question_outcomes(self):
        result = self._result()
        conditions = result['diagnostics']['reversible_conditions']
        self.assertEqual(len(conditions),
                         sum(len(question['outcomes']) for question in result['questions']))
        for condition in conditions:
            self.assertIn('winners_after', condition)


class ScaleAndCompositionTests(unittest.TestCase):
    def test_adding_dominated_candidate_does_not_change_old_relations(self):
        base = case(['a', 'b'],
                    [observation('a', 'cost', (100, 150)), observation('a', 'damage', (0.01, 0.02)),
                     observation('b', 'cost', (200, 250)), observation('b', 'damage', (0.02, 0.03))])
        expanded = case(['a', 'b', 'z'],
                        base['observations'] +
                        [observation('z', 'cost', (2500, 2600)),
                         observation('z', 'damage', (0.14, 0.15))])
        before = edges(analyze(base))
        after = {pair for pair in edges(analyze(expanded)) if 'z' not in pair}
        self.assertEqual(before, after)

    def test_relations_do_not_depend_on_the_global_scale_of_a_criterion(self):
        base = case(['a', 'b'],
                    [observation('a', 'cost', (100, 150)), observation('a', 'damage', (0.01, 0.02)),
                     observation('b', 'cost', (200, 250)), observation('b', 'damage', (0.02, 0.03))])
        scaled = case(['a', 'b'],
                      [observation('a', 'cost', (10, 15)), observation('a', 'damage', (0.01, 0.02)),
                       observation('b', 'cost', (20, 25)), observation('b', 'damage', (0.02, 0.03))])
        self.assertEqual(edges(analyze(base)), edges(analyze(scaled)))

    def test_missing_performance_is_reported_not_imputed(self):
        payload = case(['a', 'b'],
                       [observation('a', 'cost', (100, 150)), observation('a', 'damage', (0.01, 0.02))])
        result = analyze(payload)
        self.assertEqual(result['diagnostics']['incomplete_data'],
                         {'b': ['cost', 'damage']})
        self.assertEqual(result['possible_winners'], ['a'])
        missing = result['diagnostics']['still_missing']['incomplete_observations']
        self.assertEqual(missing, [{'alternative': 'b', 'criteria': ['cost', 'damage']}])

    def test_out_of_domain_observation_is_reported(self):
        criteria = [{'id': 'cost', 'direction': 'min', 'unit': 'cents_per_box', 'domain': [0, 100]}]
        payload = case(['a'], [observation('a', 'cost', (50, 150))], criteria=criteria)
        result = analyze(payload)
        flagged = result['diagnostics']['observations_outside_domain']
        self.assertEqual(flagged[0]['alternative'], 'a')
        self.assertEqual(flagged[0]['interval'], [50.0, 150.0])

    def test_missing_snapshot_marks_input_identity(self):
        payload = case(['a'], flat(['a']))
        result = analyze(payload)
        self.assertEqual(result['snapshot'], payload['_snapshot'])


class ReportingTests(unittest.TestCase):
    def test_analysis_never_claims_a_decision(self):
        result = analyze(case(['a'], flat(['a'])))
        self.assertTrue(result['diagnostics']['not_a_commitment'])
        self.assertNotIn('selected', result)
        self.assertNotIn('recommendation', result)
        for limitation in result['diagnostics']['limitations']:
            self.assertIsInstance(limitation, str)

    def test_diagnostics_describe_the_preference_family_and_interval_semantics(self):
        result = analyze(case(['a'], flat(['a'])))
        model = result['diagnostics']['preference_model']
        self.assertIn('序数', model['family'])
        self.assertIn('中点', model['orders'])
        self.assertIn('权重', model['family'])

    def test_possible_winners_basis_explains_each_winner(self):
        result = analyze(case(
            ['a', 'b'], [observation('a', 'cost', (100, 150)),
                         observation('a', 'damage', (0.01, 0.02)),
                         observation('b', 'cost', (200, 250)),
                         observation('b', 'damage', (0.02, 0.03))]))
        basis = result['diagnostics']['possible_winners_basis']
        self.assertEqual([row['alternative'] for row in basis], ['a'])
        self.assertEqual(basis[0]['beats'], ['b'])


if __name__ == '__main__':
    unittest.main()
