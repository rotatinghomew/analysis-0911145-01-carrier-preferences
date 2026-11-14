import os
import threading
import unittest

from app.client import analysis
from app.server import make_server
from app.store import load_case


@unittest.skipUnless(os.environ.get('RUN_ACCEPTANCE') == '1', 'new analysis feature not enabled')
class AnalysisContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = make_server()
        cls.worker = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.worker.start()
        cls.base = 'http://127.0.0.1:' + str(cls.server.server_port)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.worker.join(timeout=3)
        cls.server.server_close()

    def test_result_has_consumer_fields_and_provenance(self):
        result = analysis(self.base)
        self.assertEqual(result['snapshot'], load_case('standard')['_snapshot'])
        for field in ['necessary_edges', 'possible_winners', 'conflicts', 'questions', 'diagnostics']:
            self.assertIn(field, result)
        self.assertTrue(result['diagnostics'])

    def test_edge_case_is_not_silently_treated_as_standard(self):
        standard = analysis(self.base)
        edge = analysis(self.base, 'edge')
        self.assertNotEqual(standard['snapshot'], edge['snapshot'])
        self.assertTrue(edge['diagnostics'])

    def test_definite_veto_is_not_compensated_by_low_price(self):
        result = analysis(self.base)
        self.assertNotIn('fragile', result['possible_winners'])

    def test_contradictory_strict_opinions_are_reported(self):
        result = analysis(self.base, 'edge')
        self.assertTrue(result['conflicts'])

    def test_dominated_alternative_does_not_rescale_old_relations(self):
        original = analysis(self.base)
        expanded = analysis(self.base, 'dominated')
        before = {(row['better'], row['worse']) for row in original['necessary_edges']}
        after = {(row['better'], row['worse']) for row in expanded['necessary_edges'] if 'dominated' not in (row['better'], row['worse'])}
        self.assertEqual(before, after)
