from app.alternatives import comparable, performance_rows
from app.preferences import scoped_preferences
from app.store import unique


UNIT_FACTORS = {('percent', 'fraction'): 0.01, ('yuan_per_box', 'cents_per_box'): 100,
                ('minutes', 'hours'): 1 / 60}


def canonical_interval(row, criterion):
    factor = 1 if row['unit'] == criterion['unit'] else UNIT_FACTORS.get((row['unit'], criterion['unit']))
    if factor is None:
        raise ValueError('incompatible performance unit')
    return [value * factor for value in row['interval']]


def preview(case):
    lane = case['lane']
    criteria = unique(case['criteria'], 'id')
    rows = performance_rows(case, lane)
    observations = [{**row, 'interval': canonical_interval(row, criteria[row['criterion']]),
                     'unit': criteria[row['criterion']]['unit']} for row in rows.values()]
    return {'snapshot': case['_snapshot'], 'lane': lane,
            'comparable': sorted(comparable(case, lane)), 'observations': observations,
            'preferences': scoped_preferences(case, lane), 'vetoes': case['vetoes'],
            'ranking_not_computed': True}
