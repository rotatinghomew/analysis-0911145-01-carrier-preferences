def scoped_preferences(case, lane, answers=()):
    found = {}
    for row in list(case['preferences']) + list(answers):
        if row['kind'] not in ('strict', 'weak'):
            raise ValueError('unknown preference kind')
        if row['id'] in found:
            raise ValueError('preference identifiers are immutable')
        if row['better'] == row['worse'] and row['kind'] == 'strict':
            raise ValueError('strict self preference')
        found[row['id']] = dict(row)
    return [row for row in found.values() if row['lane'] == lane]
