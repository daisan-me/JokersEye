import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from contextlib import closing

spec = importlib.util.spec_from_file_location('geometry_server', Path(__file__).resolve().parents[1] / 'app/server.py')
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)


class GeometryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = server.Store(self.tmp.name)
        self.period = self.store.periods[-1]['id']
        self.payload = dict(period=self.period, imageRevision=self.store.floor_revision,
                            seat='1', x=0.4, y=0.5, angle=30, evidence='TEST ONLY: fixture, not a real seat position')

    def tearDown(self):
        self.tmp.cleanup()

    def test_no_fabricated_geometry(self):
        result = self.store.seat_map()
        self.assertEqual(result['geometry']['positions'], [])
        self.assertEqual(result['geometry']['status'], 'reference-only')
        self.assertTrue(all(s['x'] is None for s in result['seats']))

    def test_persistence_scope_and_update(self):
        self.store.save_position(self.payload)
        self.assertTrue(self.store.state()['mapRegistered'])
        restored = server.Store(self.tmp.name)
        positions = restored.seat_map()['geometry']['positions']
        self.assertEqual(len(positions), 1)
        self.assertEqual(positions[0]['angle'], 30)
        self.assertEqual(restored.seat_map(self.store.periods[-2]['id'])['geometry']['positions'], [])
        self.store.save_position(self.payload | {'x': 0.6})
        self.assertEqual(self.store.seat_map()['geometry']['positions'][0]['x'], 0.6)
        self.store.save_position(self.payload | {'remove': True})
        self.assertEqual(self.store.seat_map()['geometry']['positions'], [])

    def test_validation_atomic(self):
        for values in [{'x': float('nan')}, {'x': float('inf')}, {'x': -1}, {'y': 1.1},
                       {'angle': 181}, {'x': True}, {'evidence': ''}, {'evidence': 'x'*2001},
                       {'seat': '9999'}, {'seat': 'abc'}, {'imageRevision': 'old'}, {'period': 'missing'}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.store.save_position(self.payload | values)
        self.assertEqual(self.store.seat_map()['geometry']['positions'], [])

    def test_backup_keeps_evidence(self):
        import sqlite3
        self.store.save_position(self.payload)
        with closing(sqlite3.connect(self.store.backup())) as db:
            evidence = db.execute('SELECT evidence FROM physical_positions').fetchone()[0]
        self.assertEqual(evidence, self.payload['evidence'])


if __name__ == '__main__':
    unittest.main()
