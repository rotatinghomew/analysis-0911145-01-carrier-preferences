import unittest
from app.alternatives import comparable
from app.comparison import canonical_interval
from app.preferences import scoped_preferences
from app.store import load_case


class CarrierTests(unittest.TestCase):
    def test_service_coverage_is_not_missing_performance(self):
        self.assertNotIn('fast', comparable(load_case('standard'), 'south'))

    def test_unit_conversion_preserves_orientation(self):
        criterion = {'unit': 'fraction'}
        self.assertEqual(canonical_interval({'unit': 'percent', 'interval': [1, 3]}, criterion), [0.01, 0.03])

    def test_new_answers_do_not_overwrite_old_opinions(self):
        case = load_case('standard')
        with self.assertRaises(ValueError):
            scoped_preferences(case, 'east', [dict(case['preferences'][0])])

    def test_dimensionally_incompatible_units_fail(self):
        with self.assertRaises(ValueError):
            canonical_interval({'unit': 'hours', 'interval': [1, 2]}, {'unit': 'fraction'})
