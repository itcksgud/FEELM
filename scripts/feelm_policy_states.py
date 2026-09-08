"""Synthetic research policy simulator. No catalogue, model, API or user-data IO.

Rankings and current states are explicit inputs. This module neither trains nor
converts binary responses to the raw-rating ALS coordinate system.
"""
from __future__ import annotations
from collections import defaultdict
import math


class UnspecifiedState(ValueError):
    pass


def rating_value(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or not .5 <= value <= 5 or value * 2 != int(value * 2):
        raise ValueError('rating must be an exact half star from 0.5 to 5.0')
    return float(value)


def relative_weights(ratings, prior):
    if len(prior) != 10 or any(not math.isfinite(x) or not 0 <= x <= 1 for x in prior) or list(prior) != sorted(prior):
        raise ValueError('invalid training mid-CDF prior')
    indices = [int(rating_value(value) * 2) - 1 for value in ratings]
    histogram = [indices.count(i) for i in range(10)]
    return [(2 * sum(histogram[:i]) + histogram[i] + 10 * prior[i] - (len(indices) + 5)) / (len(indices) + 5) for i in indices]


def change_rating(state, rating):
    result = dict(state)
    if rating is None:
        if result.get('watch') != 'RATED':
            raise ValueError('cannot delete a nonexistent rating')
        result.update(rating=None, watch='WATCHED_UNRATED')
    else:
        result.update(rating=rating_value(rating), watch='RATED')
    return result


def profile(items, states, prior):
    """states is the whole observed state, not an inferred history from labels."""
    by_id = {item['id']: item for item in items}
    if len(by_id) != len(items):
        raise ValueError('duplicate movie ID')
    state_ids = [state['id'] for state in states]
    if len(set(state_ids)) != len(state_ids):
        raise ValueError('duplicate current movie state')
    if not set(state_ids) <= set(by_id):
        raise ValueError('state movie outside supplied catalogue')
    effective, rated, excluded = {}, [], set()
    popcorn, experienced = defaultdict(int), set()
    for state in states:
        mid, watch, onb = state['id'], state.get('watch'), state.get('onboarding')
        if watch == 'EXPIRED':
            raise UnspecifiedState('EXPIRED re-exposure policy is unspecified')
        if watch not in (None, 'RATED', 'WATCHED_UNRATED', 'LINK_CLICKED'):
            raise ValueError('unknown watch state')
        if onb not in (None, 'LIKE', 'DISLIKE'):
            raise ValueError('skip/unknown must be absent, not a preference response')
        rating = state.get('rating')
        if watch == 'RATED':
            rated.append((mid, rating_value(rating)))
        elif rating is not None:
            raise ValueError('rating without RATED state')
        elif onb is not None:
            effective[mid] = 1. if onb == 'LIKE' else -1.
        taste = by_id[mid].get('taste')
        if taste and watch in ('RATED', 'WATCHED_UNRATED'):
            experienced.add(taste)
        if taste and watch == 'RATED':
            popcorn[taste] += 1
        if watch is not None or onb is not None:
            excluded.add(mid)
    weights = relative_weights([value for _, value in rated], prior)
    effective.update({mid: weight for (mid, _), weight in zip(rated, weights)})
    groups = defaultdict(list)
    for mid, weight in effective.items():
        taste = by_id[mid].get('taste')
        if taste:
            groups[taste].append(weight)
    means = {taste: math.fsum(values) / len(values) for taste, values in groups.items()}
    positive = {taste for taste, value in means.items() if value > 0}
    anchors = {mid for mid, weight in effective.items() if weight > 0 and by_id[mid].get('taste') in positive}
    return {'effective': effective, 'positive_tastes': positive, 'experienced_tastes': experienced,
            'popcorn': dict(popcorn), 'taste_means': means, 'anchors': anchors, 'excluded': excluded,
            'input_movies': len(effective), 'unassigned_input_movies': sum(not by_id[mid].get('taste') for mid in effective)}


def connection(candidate, anchors, by_id):
    """Return checkable content facts; these are not preference labels."""
    facts = []
    for anchor_id in sorted(anchors):
        anchor = by_id[anchor_id]
        for field in ('genres', 'directors', 'keywords'):
            for term in sorted(set(candidate.get(field, ())) & set(anchor.get(field, ()))):
                facts.append({'anchor_movie_id': anchor_id, 'field': field, 'id': term})
    return facts


def ordered_pool(items, scores):
    by_id = {item['id']: item for item in items}
    if any(mid not in by_id for mid in scores) or any(not math.isfinite(value) for value in scores.values()):
        raise ValueError('invalid supplied score')
    return sorted(scores, key=lambda mid: (-scores[mid], mid))


def pools(items, states, prior, scores, blocked=(), available=None):
    p = profile(items, states, prior)
    by_id = {item['id']: item for item in items}
    order = ordered_pool(items, scores)
    excluded = p['excluded'] | set(blocked)
    allowed = set(by_id) if available is None else set(available)
    if not allowed <= set(by_id):
        raise ValueError('candidate pool outside supplied catalogue')
    taste, discovery, facts = [], [], {}
    for mid in order:
        if mid in excluded or mid not in allowed:
            continue
        item = by_id[mid]; code = item.get('taste')
        if not code:
            continue
        if code in p['positive_tastes']:
            taste.append(mid)
        elif code not in p['experienced_tastes']:
            shared = connection(item, p['anchors'], by_id)
            if shared:
                discovery.append(mid); facts[mid] = shared
    return p, taste, discovery, facts


def _select(items, states, prior, scores, popularity, blocked=(), available=None):
    blocked = frozenset(blocked)
    available = None if available is None else frozenset(available)
    p, taste, discovery, facts = pools(items, states, prior, scores, blocked, available)
    if not p['input_movies']:
        allowed = {item['id'] for item in items} if available is None else set(available)
        assigned = {item['id'] for item in items if item.get('taste')}
        chosen = [mid for mid in ordered_pool(items, popularity) if mid not in p['excluded'] | blocked and mid in allowed & assigned][:3]
        return {'status': 'NO_INPUT_POPULAR' if len(chosen) == 3 else 'INSUFFICIENT_POPULAR',
                'items': [{'id': mid, 'type': 'TASTE'} for mid in chosen], 'input_movies': 0,
                'research_proposal': True, 'personalized': False, 'final_K': None,
                'unassigned_in_available_pool': len(allowed - assigned)}
    status = 'NO_POSITIVE_TASTE' if not p['positive_tastes'] else 'INSUFFICIENT_TASTE'
    if len(taste) < 2:
        return {'status': status, 'items': [], 'taste_supply': len(taste), 'discovery_supply': len(discovery)}
    if discovery:
        return {'status': 'T2_D1', 'items': [{'id': mid, 'type': 'TASTE'} for mid in taste[:2]] +
                [{'id': discovery[0], 'type': 'DISCOVERY', 'connection_facts': facts[discovery[0]]}]}
    if len(taste) < 3:
        return {'status': 'INSUFFICIENT_TASTE_FALLBACK', 'items': [], 'taste_supply': len(taste), 'discovery_supply': 0}
    return {'status': 'NO_DISCOVERY_T3', 'items': [{'id': mid, 'type': 'TASTE'} for mid in taste[:3]]}


def recommend(items, states, prior, scores, popularity, blocked=(), available=None, *, ranking_mode):
    count = profile(items, states, prior)['input_movies']
    expected = 'NO_INPUT_POPULAR_EXAMPLE' if count == 0 else 'FORCED_RANK_DIAGNOSTIC'
    if ranking_mode != expected:
        raise UnspecifiedState('supply the explicit research ranking mode; no K transition policy is implemented')
    result = _select(items, states, prior, scores, popularity, blocked, available)
    result.update(ranking_mode=ranking_mode, research_proposal=True, final_K=None, input_movies=count)
    return result


def replace_from_buffer(buffer, rejected_type, blocked):
    """Current set's typed buffer only. No rescore, model update, or new set."""
    if rejected_type not in ('TASTE', 'DISCOVERY'):
        raise ValueError('unknown recommendation type')
    ids = [item['id'] for item in buffer]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate buffer movie')
    blocked = frozenset(blocked)
    return next((dict(item) for item in buffer if item['type'] == rejected_type and item['id'] not in blocked), None)
