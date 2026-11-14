from app.store import unique


def comparable(case, lane):
    alternatives = unique(case['alternatives'], 'id')
    return {key: value for key, value in alternatives.items() if lane in value['coverage']}


def performance_rows(case, lane):
    candidates = comparable(case, lane)
    criteria = unique(case['criteria'], 'id')
    found = {}
    for row in case['observations']:
        if row['lane'] != lane or row['alternative'] not in candidates:
            continue
        lower, upper = row['interval']
        if lower > upper or row['criterion'] not in criteria:
            raise ValueError('invalid performance interval')
        key = (row['alternative'], row['criterion'])
        if key in found:
            raise ValueError('ambiguous performance revision')
        found[key] = dict(row)
    return found
