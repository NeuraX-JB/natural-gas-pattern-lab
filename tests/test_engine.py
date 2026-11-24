import csv
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from gaslab.engine import find_analogues, load_rows, match_exact


class EngineTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)

    def write(self, name, columns, rows):
        path = self.root / name
        with path.open('w', newline='') as file:
            writer = csv.writer(file)
            writer.writerow(columns.split(','))
            writer.writerows(rows)
        return path

    def prices(self, start=date(2020, 1, 1), n=150):
        price = Decimal('3.00')
        prices = []
        moves = [8, 2, -3, 1, -7, 2, -1]
        for i in range(n):
            price += Decimal(moves[i % len(moves)]) / 100
            prices.append((start + timedelta(days=i), price))
        return self.write('spot.csv', 'date,price', prices)

    def test_matured_only_and_distinct_nonoverlap(self):
        rows = load_rows(self.prices())
        cutoff = 91
        result = match_exact(rows, rows[cutoff]['date'], ['season'])
        index = {row['date']: i for i, row in enumerate(rows)}
        self.assertLessEqual(max(index[r['date']] for r in result['occurrences']) + 10, cutoff - 1)
        # Override query's season to force a dense sequence of eligible dates.
        result = match_exact(rows, rows[cutoff]['date'], ['season'], {'season': 'winter'})
        anchors = [index[r['date']] for r in result['matches']]
        self.assertGreater(len(result['occurrences']), len(anchors))
        self.assertTrue(all(b - a >= 16 for a, b in zip(anchors, anchors[1:])))
        for match in result['matches']:
            i = index[match['date']]
            self.assertEqual(match['path'][0], 100.0)
            self.assertEqual(match['outcomes']['10'], round(rows[i+10]['price'] - rows[i]['price'], 10))

    def test_exact_magnitude_not_just_direction(self):
        rows = load_rows(self.prices())
        cutoff = rows[100]['date']
        direction = match_exact(rows, cutoff, ['move'], {'move': 'up'})
        exact = match_exact(rows, cutoff, ['move', 'move_cents'], {'move': 'Up', 'move_cents': '+8'})
        self.assertGreater(len(direction['occurrences']), len(exact['occurrences']))
        self.assertGreater(len(exact['occurrences']), 1)
        self.assertTrue(all(m['features']['move_cents'] == '+8' for m in exact['occurrences']))

    def test_future_edits_do_not_change_neighbors_or_summary(self):
        rows = load_rows(self.prices())
        day = rows[80]['date']
        cropped = rows[:81]
        a = find_analogues(rows, day)
        b = find_analogues(cropped, day)
        self.assertEqual(a, b)
        self.assertTrue(a['matches'])
        self.assertTrue(all(m['distance'] <= a['max_distance'] for m in a['matches']))
        self.assertTrue(all('components' in m and 'biggest_difference' in m for m in a['matches']))
        ixs = sorted(next(i for i, row in enumerate(rows) if row['date'] == match['date']) for match in a['matches'])
        self.assertTrue(all(n - m >= 16 for m, n in zip(ixs, ixs[1:])))
        no_paths = find_analogues(rows, day, include_paths=False)
        self.assertNotIn('path', no_paths['matches'][0])
        self.assertEqual(a['summary'], no_paths['summary'])

    def test_late_storage_publication_vintages_and_unverified_data(self):
        spot = self.prices(date(2025, 1, 2), 15)
        prior = []
        for year in range(2020, 2025):
            period = date.fromisocalendar(year, 1, 5)
            prior.append((period, f'{period}T18:00:00Z', '100', 'true'))
        final = date.fromisocalendar(2025, 1, 5)
        records = prior + [(final, '2025-01-04T16:00:00Z', '999', 'false'),
                           (final, '2025-01-07T20:00:00Z', '95', 'true'),
                           (final, '2025-01-10T20:00:00Z', '105', 'true')]
        cols = 'period_end,published_at,inventory_bcf,verified'
        storage = self.write('w.csv', cols, records)
        rows = {row['date']: row for row in load_rows(spot, storage)}
        self.assertIsNone(rows['2025-01-06']['features']['storage'])
        self.assertEqual(rows['2025-01-07']['metrics']['storage_gap'], -5)
        self.assertEqual(rows['2025-01-10']['metrics']['storage_gap'], 5)
        missing = self.write('missing.csv', cols, records[1:])
        self.assertIsNone(load_rows(spot, missing)[6]['features']['storage'])

    def test_weather_requires_matching_dates_and_known_availability(self):
        spot = self.prices(date(2025, 1, 1), 5)
        cols = 'previous_run_at,current_run_at,available_at,valid_date,hdd_previous,hdd_current,cdd_previous,cdd_current,verified'
        records = [('2025-01-01T06:00Z', '2025-01-02T06:00Z', '2025-01-02T09:00Z', f'2025-01-{i:02d}', 5, 7, 0, 1, 'true') for i in range(3, 8)]
        complete = load_rows(spot, weather_path=self.write('complete.csv', cols, records))
        self.assertEqual(complete[1]['metrics']['hdd_revision'], 10)
        self.assertEqual(complete[1]['features']['weather'], 'HDD Up / CDD Up')
        self.assertIsNone(complete[0]['features']['weather'])
        late = list(records[-1])
        late[2] = '2025-01-03T10:00Z'
        incomplete = load_rows(spot, weather_path=self.write('late.csv', cols, records[:-1]+[late]))
        self.assertIsNone(incomplete[1]['features']['weather'])

    def test_feature_warmup_and_missing_states_never_match(self):
        rows = load_rows(self.prices())
        self.assertIsNone(rows[64]['features']['volatility'])
        self.assertIsNotNone(rows[65]['features']['volatility'])
        self.assertEqual(match_exact(rows, rows[-1]['date'], ['storage'])['status'], 'unavailable_features')
        self.assertEqual(find_analogues(rows, rows[0]['date'])['status'], 'unavailable_features')
        self.assertEqual(find_analogues(rows, rows[-1]['date'], weights={'storage_gap': 1})['matches'], [])

    def test_sequence_order_direction_and_literal_magnitude(self):
        rows = load_rows(self.prices())
        cutoff = rows[-1]['date']
        exact = match_exact(rows, cutoff, sequence=['DOWN', '+08'])
        reverse = match_exact(rows, cutoff, sequence=['+8', 'down'])
        up = match_exact(rows, cutoff, sequence=['down', 'Up'])
        self.assertEqual(exact['sequence'], ['Down', '+8'])
        self.assertEqual(exact['fields'], [])
        as_dates = lambda result: {m['date'] for m in result['occurrences']}
        self.assertTrue(as_dates(exact))
        self.assertTrue(as_dates(exact).isdisjoint(as_dates(reverse)))
        self.assertLess(as_dates(exact), as_dates(up))
        self.assertEqual(as_dates(exact), as_dates(match_exact(rows, cutoff, ['prior_move','move_cents'],
                                       {'prior_move':'Down','move_cents':'+8'})))
        constrained = match_exact(rows, cutoff, ['season'], {'season':'Winter'}, sequence=['Down', '+8'])
        self.assertLess(as_dates(constrained), as_dates(exact))

    def test_ten_step_sequence_needs_history_and_extended_spacing(self):
        start = date(2021, 1, 1)
        rows = load_rows(self.write('rising.csv', 'date,price', [
            (start + timedelta(days=i), Decimal(1) + Decimal(i)/100) for i in range(120)]))
        target = rows[-1]['date']
        result = match_exact(rows, target, [], sequence=['Up'] * 7 + ['*', '+1', 'Up'])
        self.assertEqual(result['occurrences'][0]['date'], rows[10]['date'])
        self.assertEqual(result['min_separation'], 21)
        self.assertEqual([r['date'] for r in result['matches']], [rows[i]['date'] for i in (10, 31, 52, 73, 94)])
        seven = match_exact(rows, target, [], sequence=['+1', '*', 'Up', 'Up', '+1', '+1', '+1'])
        self.assertEqual(seven['occurrences'][0]['date'], rows[7]['date'])
        self.assertEqual(seven['min_separation'], 18)
        for bad in ([], ['Up']*11, ['unknown'], ['*', '*'], 'Up', ['8']):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    match_exact(rows, target, [], sequence=bad)
        with self.assertRaises(ValueError):
            match_exact(rows, target, [])

    def test_official_storage_comparator_and_revised_omission(self):
        spot = self.prices(date(2025, 1, 2), 15)
        cols = 'period_end,published_at,inventory_bcf,five_year_avg_bcf,verified'
        storage = self.write('w.csv', cols, [('2025-01-03','2025-01-06T15:00Z',140,130,'true'),
                                             ('2025-01-03','2025-01-09T15:00Z',139,'','true')])
        rows = {r['date']:r for r in load_rows(spot, storage)}
        self.assertEqual(rows['2025-01-06']['metrics']['storage_gap'], 10)
        self.assertIsNone(rows['2025-01-09']['metrics']['storage_gap'])
