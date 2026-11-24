"""Point-in-time features, exact event matching, and numeric historical analogues.

A session means one published spot-price observation. Cutoffs are the end of the
observation date in New York. See the data notes for the price series' vintage.
"""
from bisect import bisect_left
from collections import defaultdict, deque
import csv
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from math import isfinite, sqrt
import re
from statistics import median, pstdev
from .timezones import NEW_YORK as NY

HORIZONS = (1, 5, 10)
DEFAULT_FIELDS = ("prior_move", "move", "trend_5")
EXACT_FIELDS = {
    "season": "Season",
    "prior_move": "Previous one-session move",
    "move": "Latest one-session move",
    "move_cents": "Latest move (signed cents, rounded)",
    "trend_5": "Five-session direction",
    "volatility": "Five-session volatility regime",
    "storage": "Storage versus five-year average",
    "weather": "Five-day HDD/CDD revision",
}
METRICS = {
    "season": "Season",
    "change_1": "One-session price change",
    "change_5": "Five-session price change",
    "vol_5": "Five-session volatility",
    "storage_gap": "Storage gap (Bcf)",
    "hdd_revision": "Five-day HDD revision",
    "cdd_revision": "Five-day CDD revision",
}
DEFAULT_WEIGHTS = dict.fromkeys(METRICS, 1.0)
MAX_DISTANCE = 1.0
# Full attribution windows [anchor - 5, anchor + 10] cannot share endpoints.
MIN_SEPARATION = 16


def _direction(value):
    if value is None:
        return None
    if value > 0:
        return "Up"
    return "Down" if value < 0 else "Flat"


def _finite(value):
    result = float(value)
    if not isfinite(result):
        raise ValueError(f"Nonfinite numeric input: {value!r}")
    return result


def _timestamp(value):
    parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if parsed.utcoffset() is None:
        raise ValueError(f"Provide an offset or Z for timestamp {value!r}")
    return parsed.astimezone(timezone.utc)


def _records(path, required):
    if path is None:
        return
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = set(required).difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"{path} is missing columns: {', '.join(sorted(missing))}")
        yield from reader


def _verified(row):
    flag = row['verified'].strip().lower()
    if flag not in {'true', 'false', '1', '0', 'yes', 'no', ''}:
        raise ValueError(f"Unrecognized verified flag {row['verified']!r}")
    return flag in {'true', 'yes', '1'}


def _read_storage(path):
    source = _records(path, ('period_end', 'published_at', 'inventory_bcf', 'verified'))
    releases = {}
    for row in source:
        if not _verified(row):
            continue
        period = date.fromisoformat(row['period_end'])
        stamp = _timestamp(row['published_at'])
        if stamp.astimezone(NY).date() < period:
            raise ValueError('A verified storage release predates its period end')
        key = (stamp, period)
        comparison = (row.get('five_year_avg_bcf') or '').strip()
        value = (_finite(row['inventory_bcf']), _finite(comparison) if comparison else None)
        if key in releases and releases[key] != value:
            raise ValueError(f'Conflicting storage observations for {key}')
        releases[key] = value
    return sorted((stamp, period, value) for (stamp, period), value in releases.items())


def _storage_gap(latest, inventory, day):
    if latest is None or (day - latest).days > 15:
        return None
    year, week, _ = latest.isocalendar()
    current, official_mean = inventory[(year, week)]
    if official_mean is not None:
        return round(current - official_mean, 8)
    prior = [inventory.get((y, week), (None, None))[0] for y in range(year - 5, year)]
    if any(value is None for value in prior):
        return None
    return round(current - sum(prior) / 5, 8)


def _read_weather(path):
    names = ('previous_run_at', 'current_run_at', 'available_at', 'valid_date',
             'hdd_previous', 'hdd_current', 'cdd_previous', 'cdd_current', 'verified')
    data = defaultdict(dict)
    for row in _records(path, names):
        if not _verified(row):
            continue
        prior, current, available = (_timestamp(row[name]) for name in names[:3])
        valid = date.fromisoformat(row['valid_date'])
        if not prior < current <= available:
            raise ValueError('Weather run times must precede their public availability')
        if (current - prior) > timedelta(hours=48):
            continue
        values = [_finite(row[name]) for name in names[4:8]]
        if min(values) < 0:
            raise ValueError('Archived degree-day forecasts must be nonnegative')
        record = (available, values[1] - values[0], values[3] - values[2])
        key = (current, prior)
        if valid in data[key] and data[key][valid] != record:
            raise ValueError(f'Conflicting aligned weather records for {key}, {valid}')
        data[key][valid] = record
    # Each current run can be used only within 48 hours. Index by potential query day.
    by_day = defaultdict(list)
    for key, aligned in sorted(data.items(), reverse=True):
        run_day = key[0].astimezone(NY).date()
        for offset in range(3):
            by_day[run_day + timedelta(days=offset)].append((key, aligned))
    return by_day


def _weather_revisions(by_day, day, cutoff):
    for (current, _), aligned in by_day.get(day, ()):
        if not timedelta(0) <= cutoff - current <= timedelta(hours=48):
            continue
        days = [aligned.get(day + timedelta(days=i)) for i in range(1, 6)]
        if all(item is not None and item[0] <= cutoff for item in days):
            return tuple(round(sum(item[k] for item in days), 8) for k in (1, 2))
    return None, None


def load_rows(spot_path, storage_path=None, weather_path=None):
    """Read CSVs and derive features using only data available by each cutoff.

    Spot CSV requires date,price (ISO date, USD/MMBtu). Storage CSV requires
    period_end,published_at,inventory_bcf,verified. An optional five_year_avg_bcf
    supplies the benchmark as printed in the verified release; otherwise use
    the mean of the same ISO week in all five previous ISO years as known at
    the cutoff. Storage becomes stale 15 calendar days after its period end.

    Optional weather CSV (one model and one U.S. population-weighted region)
    needs previous_run_at,current_run_at,available_at,valid_date,hdd_previous,
    hdd_current,cdd_previous,cdd_current,verified. Each row pairs two forecasts
    for one identical valid date. Archived degree days should use a 65°F base.
    Each run timestamp and the earliest provable availability of both forecasts
    must include a UTC offset. All five valid dates immediately after the
    query date must exist, each with verified status and availability by the
    New York cutoff. Select the latest eligible pair (current run no more than
    48h old, prior run within 48h of current), summing new minus old values
    separately for heating and cooling. Empty/unverified data is unavailable.
    """
    prices = {}
    for entry in _records(spot_path, ('date', 'price')):
        day = date.fromisoformat(entry['date'])
        if day in prices:
            raise ValueError(f'Duplicate spot observation date: {day}')
        _finite(entry['price'])
        prices[day] = Decimal(entry['price'])
    releases = _read_storage(storage_path)
    weather = _read_weather(weather_path)
    rows, changes = [], []
    history = deque(maxlen=252)
    inventory, latest, release_index = {}, None, 0
    # Welford moments supply the numeric scales available before each row.
    moments = {key: (0, 0.0, 0.0) for key in METRICS if key != 'season'}
    days = sorted(prices)
    for i, day in enumerate(days):
        price = prices[day]
        delta = price - prices[days[i - 1]] if i else None
        changes.append(delta)
        trend = price - prices[days[i - 5]] if i >= 5 else None
        vol = pstdev(map(float, changes[-5:])) if i >= 5 else None
        vol_state = None
        if vol is not None:
            if len(history) >= 60:
                past = sorted(history)
                lower, upper = (past[int(p * (len(past) - 1))] for p in (0.25, 0.75))
                vol_state = 'Low' if vol < lower else 'High' if vol > upper else 'Normal'
            history.append(vol)
        cutoff = datetime.combine(day, time.max, NY).astimezone(timezone.utc)
        while release_index < len(releases) and releases[release_index][0] <= cutoff:
            _, period, value = releases[release_index]
            inventory[period.isocalendar()[:2]] = value
            latest = period if latest is None else max(period, latest)
            release_index += 1
        gap = _storage_gap(latest, inventory, day)
        hdd, cdd = _weather_revisions(weather, day, cutoff)
        cents = int((delta * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)) if i else None
        cent_label = None if cents is None else f'{cents:+d}' if cents else '0'
        season = ('Winter', 'Spring', 'Summer', 'Autumn')[(day.month % 12) // 3]
        features = {
            'season': season, 'prior_move': _direction(changes[i-1]) if i else None,
            'move': _direction(delta), 'move_cents': cent_label, 'trend_5': _direction(trend),
            'volatility': vol_state,
            'storage': None if gap is None else 'Surplus' if gap > 0 else 'Deficit' if gap < 0 else 'At average',
            'weather': None if hdd is None else f'HDD {_direction(hdd)} / CDD {_direction(cdd)}',
        }
        numeric = {'change_1': float(delta) if delta is not None else None,
                   'change_5': float(trend) if trend is not None else None,
                   'vol_5': vol, 'storage_gap': gap, 'hdd_revision': hdd, 'cdd_revision': cdd}
        scales = {key: sqrt(max(m2, 0) / n) if n else None for key, (n, _, m2) in moments.items()}
        rows.append({'date': day.isoformat(), 'price': float(price), 'features': features,
                     'metrics': numeric, 'scales': scales})
        for key, value in numeric.items():
            if value is not None:
                n, mean, m2 = moments[key]
                n += 1
                new_mean = mean + (value - mean) / n
                moments[key] = (n, new_mean, m2 + (value - mean) * (value - new_mean))
    return rows


def _index(rows, as_of):
    stamp = as_of.isoformat() if isinstance(as_of, date) else str(as_of)
    i = bisect_left(rows, stamp, key=lambda row: row['date'])
    if i == len(rows) or rows[i]['date'] != stamp:
        raise ValueError(f'{stamp} is not a spot observation date')
    return i


def _public_row(row):
    return {key: row[key] for key in ('date', 'price', 'features', 'metrics')}


def _episode(rows, i, include_paths=True):
    result = _public_row(rows[i])
    anchor = rows[i]['price']
    result['outcomes'] = {str(h): round(rows[i+h]['price'] - anchor, 10) for h in HORIZONS}
    result['outcome_dates'] = {str(h): rows[i+h]['date'] for h in HORIZONS}
    if include_paths:
        result['path'] = [round(100 * rows[j]['price'] / anchor, 6) if anchor else None
                          for j in range(i, i+11)]
    return result


def summarize(matches):
    summary = {'count': len(matches), 'horizons': {}}
    for h in HORIZONS:
        values = [row['outcomes'][str(h)] for row in matches]
        up, down = sum(v > 0 for v in values), sum(v < 0 for v in values)
        summary['horizons'][str(h)] = {
            'median': median(values) if values else None, 'up': up, 'down': down,
            'flat': len(values) - up - down, 'up_rate': up/len(values) if values else None,
            'min': min(values, default=None), 'max': max(values, default=None),
        }
    return summary


def _status(matches):
    if len(matches) >= 3:
        return 'ok'
    return 'insufficient_matches' if matches else 'no_matches'


def _normalize_sequence(sequence):
    if sequence is None:
        return []
    if not isinstance(sequence, (list, tuple)) or not 1 <= len(sequence) <= 10:
        raise ValueError('Supply a sequence of 1 to 10 ordered moves')
    tokens = []
    for raw in sequence:
        token = str(raw).strip()
        if token == '*':
            tokens.append(token)
        elif token.title() in ('Up', 'Down', 'Flat'):
            tokens.append(token.title())
        elif re.fullmatch(r'[+-][0-9]{1,9}|0', token):
            cents = int(token)
            tokens.append(f'{cents:+d}' if cents else '0')
        else:
            raise ValueError(f'Unknown move {token!r}; use Up, Down, Flat, signed cents, 0 or *')
    if all(token == '*' for token in tokens):
        raise ValueError('The sequence needs at least one specified move')
    return tokens


def _matches_sequence(rows, end, tokens):
    # A move ending on row 1 uses both row 0 and row 1. Row 0 has no known move.
    if end < len(tokens):
        return False
    for i, token in enumerate(tokens, start=end - len(tokens) + 1):
        features = rows[i]['features']
        if token not in ('*', features['move'], features['move_cents']):
            return False
    return True


def match_exact(rows, as_of, fields=None, conditions=None, include_paths=True, sequence=None):
    """Every eligible old date with identical selected values; thin separately.

    Passing conditions overrides any or all selected query-date values. Full
    occurrences are chronological and can cluster. The nonoverlapping summary only
    includes earliest nonoverlapping windows, retained before seeing outcomes.
    Sequence tokens are consecutive moves ending on the anchor, oldest first;
    each token may be a move direction, rounded signed cents, or a wildcard.
    """
    qi = _index(rows, as_of)
    tokens = _normalize_sequence(sequence)
    overrides = conditions or {}
    if fields is None:
        fields = tuple(overrides) if overrides else () if tokens else DEFAULT_FIELDS
    fields = tuple(dict.fromkeys(fields))
    if set(fields).difference(EXACT_FIELDS) or not (fields or tokens):
        raise ValueError('Select at least one recognized condition or an ordered sequence')
    if set(overrides).difference(fields):
        raise ValueError('Every override must be among the selected fields')
    target = {key: overrides.get(key, rows[qi]['features'][key]) for key in fields}
    occurrences, matches = [], []
    separation = max(5, len(tokens)) + max(HORIZONS) + 1
    last = -separation
    missing = [name for name in fields if target[name] is None]
    folded = {key: str(value).casefold() for key, value in target.items()}
    if not missing:
        for i in range(max(0, qi - max(HORIZONS))):
            observed = rows[i]['features']
            if not _matches_sequence(rows, i, tokens):
                continue
            if all(observed[key] is not None and str(observed[key]).casefold() == folded[key] for key in fields):
                episode = _episode(rows, i, include_paths)
                occurrences.append(episode)
                if i - last >= separation:
                    matches.append(episode)
                    last = i
    return {'query': _public_row(rows[qi]), 'fields': list(fields), 'conditions': target,
            'occurrences': occurrences, 'matches': matches, 'summary': summarize(matches),
            'sequence': tokens, 'min_separation': separation,
            'status': 'unavailable_features' if missing else _status(matches), 'unavailable': missing}


def _component(candidate, query, key, scale):
    if key == 'season':
        return float(candidate['features']['season'] != query['features']['season'])
    v = candidate['metrics'][key]
    if v is None:
        return None
    delta = abs(v - query['metrics'][key])
    if scale is None or scale <= 1e-12:
        return 0.0 if delta <= 1e-12 else None
    return delta / scale


def _explain_component(key, candidate, query, gap):
    if key == 'season':
        return f"Season: {candidate['features']['season']} vs {query['features']['season']} at query"
    units = {'change_1': '$/MMBtu', 'change_5': '$/MMBtu', 'vol_5': '$/MMBtu',
             'storage_gap': 'Bcf', 'hdd_revision': 'HDD', 'cdd_revision': 'CDD'}
    past, current = candidate['metrics'][key], query['metrics'][key]
    return f'{METRICS[key]}: {past:.3g} vs {current:.3g} {units[key]} at query (scaled gap {gap:.2f})'


def find_analogues(rows, as_of, limit=10, weights=None, include_paths=True, max_distance=MAX_DISTANCE,
                   include_weather=True):
    """Rank nonoverlapping past episodes by weighted RMS standardized difference.

    Scales use only earlier eligible anchor dates. Season costs zero for the
    same quarter and one otherwise. Equal fixed weights apply to available query
    metrics by default. Each candidate needs all active dimensions. A maximum
    RMS distance of 1.0 withholds weak matches, even if fewer than ten remain.
    """
    if not isinstance(limit, int) or not 1 <= limit <= 10:
        raise ValueError('Choose an analogue limit between 1 and 10')
    if not isfinite(max_distance) or max_distance < 0:
        raise ValueError('Choose a finite, nonnegative distance limit')
    qi = _index(rows, as_of)
    query = rows[qi]
    requested = DEFAULT_WEIGHTS if weights is None else weights
    if not include_weather:
        requested = {k: v for k, v in requested.items() if k not in ("hdd_revision", "cdd_revision")}
    if not requested or set(requested).difference(METRICS):
        raise ValueError('Provide known analogue metric weights')
    active = {k: _finite(v) for k, v in requested.items()}
    if any(w < 0 for w in active.values()) or not any(active.values()):
        raise ValueError('Weights must be nonnegative with at least one positive weight')
    active = {k: w for k, w in active.items() if w}
    missing = [k for k in active if k != 'season' and query['metrics'][k] is None]
    required_missing = missing if weights is not None else [k for k in missing if k in ('change_1','change_5','vol_5')]
    active = {k: w for k, w in active.items() if k not in missing}
    eligible = max(0, qi - max(HORIZONS))
    scales = rows[eligible]['scales']
    weight_total = sum(active.values())
    scores, matches, chosen = [], [], []
    if not required_missing and weight_total:
        for i in range(eligible):
            total = 0.0
            for key, weight in active.items():
                gap = _component(rows[i], query, key, scales.get(key))
                if gap is None:
                    break
                total += weight * gap * gap
            else:
                distance = sqrt(total / weight_total)
                if distance <= max_distance:
                    scores.append((distance, i))
    for distance, i in sorted(scores):
        if any(abs(previous - i) < MIN_SEPARATION for previous in chosen):
            continue
        chosen.append(i)
        parts = {key: _component(rows[i], query, key, scales.get(key)) for key in active}
        ordered = sorted(parts, key=lambda key: active[key] * parts[key]**2)
        episode = _episode(rows, i, include_paths)
        episode.update(distance=distance, components=parts,
                       reasons=[_explain_component(k, rows[i], query, parts[k]) for k in ordered[:2]],
                       biggest_difference=(_explain_component(ordered[-1], rows[i], query, parts[ordered[-1]])
                                           if distance else 'Identical on all active metrics'))
        matches.append(episode)
        if len(matches) == limit:
            break
    return {'query': _public_row(query), 'weights': active, 'matches': matches,
            'summary': summarize(matches), 'max_distance': max_distance,
            'status': 'unavailable_features' if required_missing else _status(matches),
            'unavailable': missing, 'scales': {k: scales.get(k) for k in active if k != 'season'}}
