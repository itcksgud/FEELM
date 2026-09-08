"""Typed current-snapshot boundary; no DB, events payloads, or model implementation."""
from dataclasses import dataclass
from feelm_policy_states import rating_value


def strict_keys(value, expected):
    if not isinstance(value, dict) or set(value) != set(expected):
        raise ValueError('unexpected or missing input fields')


def integer(value, minimum=0):
    if not isinstance(value, int) or isinstance(value, bool) or value < minimum:
        raise ValueError('invalid integer')
    return value


def unique_rows(rows, fields):
    if not isinstance(rows, list): raise ValueError('expected a current-state list')
    result = {}
    for row in rows:
        strict_keys(row, fields); mid = integer(row['movie_id'], 1)
        if mid in result: raise ValueError('duplicate current movie input')
        result[mid] = row
    return result


@dataclass(frozen=True)
class RawRatingPacket:
    observations: tuple
    namespace: str
    origin: str
    version: int


@dataclass(frozen=True)
class BinaryPacket:
    observations: tuple
    namespace: str
    origin: str
    version: int


@dataclass(frozen=True)
class CurrentInputs:
    raw: RawRatingPacket
    binary: BinaryPacket
    original_onboarding: tuple
    watch_states: tuple
    unique_effective_movies: int


def validate_pairs(rows, value_validator):
    if type(rows) is not tuple: raise ValueError('immutable tuple observations required')
    result = {}
    for row in rows:
        if type(row) is not tuple or len(row) != 2: raise ValueError('invalid observation pair')
        mid, value = row
        integer(mid, 1)
        if mid in result: raise ValueError('duplicate movie ID')
        value_validator(value)
        result[mid] = value
    return result


def enum_value(value, allowed):
    if type(value) is not str or value not in allowed: raise ValueError('invalid enum')


def validate_packet(packet, expected_type):
    if type(packet) is not expected_type: raise TypeError('incorrect tagged packet type')
    enum_value(packet.namespace, ('TMDB', 'MOVIELENS'))
    enum_value(packet.origin, ('ACTUAL_FEELM', 'MOVIELENS_RAW_PROXY', 'MOVIELENS_BINARY_PROXY'))
    integer(packet.version)
    if packet.origin.startswith('MOVIELENS_') and packet.namespace != 'MOVIELENS':
        raise ValueError('proxy namespace mismatch')
    validator = rating_value if expected_type is RawRatingPacket else lambda v: enum_value(v, ('LIKE', 'DISLIKE'))
    values = validate_pairs(packet.observations, validator)
    forbidden = 'MOVIELENS_BINARY_PROXY' if expected_type is RawRatingPacket else 'MOVIELENS_RAW_PROXY'
    if values and packet.origin == forbidden: raise ValueError('origin and observation type differ')
    return values


def validate_current(inputs, expected_namespace):
    if type(inputs) is not CurrentInputs: raise TypeError('current inputs required')
    raw = validate_packet(inputs.raw, RawRatingPacket)
    binary = validate_packet(inputs.binary, BinaryPacket)
    a, b = inputs.raw, inputs.binary
    if (a.namespace, a.origin, a.version) != (b.namespace, b.origin, b.version):
        raise ValueError('packets come from different snapshots')
    if a.namespace != expected_namespace: raise ValueError('movie namespace mismatch')
    original = validate_pairs(inputs.original_onboarding, lambda v: enum_value(v, ('LIKE', 'DISLIKE')))
    watches = validate_pairs(inputs.watch_states, lambda v: enum_value(v, ('RATED', 'WATCHED_UNRATED', 'LINK_CLICKED', 'EXPIRED')))
    if original and a.origin == 'MOVIELENS_RAW_PROXY': raise ValueError('raw proxy contains binary responses')
    if set(raw) != {mid for mid, status in watches.items() if status == 'RATED'}:
        raise ValueError('current ratings and RATED records differ')
    if binary != {mid: response for mid, response in original.items() if mid not in raw}:
        raise ValueError('effective binary observations violate rating priority')
    if integer(inputs.unique_effective_movies) != len(raw) + len(binary):
        raise ValueError('effective input count differs')


def normalize(snapshot, event_version):
    strict_keys(snapshot, ('input_version','id_namespace','origin','ratings','onboarding','watch_states'))
    version = integer(snapshot['input_version']); integer(event_version)
    if version < event_version: raise ValueError('current snapshot is older than the triggering event')
    ns, origin = snapshot['id_namespace'], snapshot['origin']
    if ns not in ('TMDB','MOVIELENS'): raise ValueError('unknown movie ID namespace')
    if origin not in ('ACTUAL_FEELM','MOVIELENS_RAW_PROXY','MOVIELENS_BINARY_PROXY'): raise ValueError('unknown input origin')
    if origin.startswith('MOVIELENS_') and ns != 'MOVIELENS': raise ValueError('proxy namespace mismatch')
    ratings = unique_rows(snapshot['ratings'], ('movie_id','rating'))
    onboarding = unique_rows(snapshot['onboarding'], ('movie_id','response'))
    watches = unique_rows(snapshot['watch_states'], ('movie_id','status'))
    if origin == 'MOVIELENS_BINARY_PROXY' and ratings: raise ValueError('binary proxy must not contain hidden raw ratings')
    if origin == 'MOVIELENS_RAW_PROXY' and onboarding: raise ValueError('raw proxy must not masquerade as binary responses')
    for row in onboarding.values():
        if row['response'] not in ('LIKE','DISLIKE'): raise ValueError('invalid binary response')
    for row in watches.values():
        if row['status'] not in ('RATED','WATCHED_UNRATED','LINK_CLICKED','EXPIRED'): raise ValueError('unknown watch status')
    if set(ratings) != {mid for mid,row in watches.items() if row['status']=='RATED'}:
        raise ValueError('current ratings and RATED records differ')
    raw = tuple((mid,rating_value(row['rating'])) for mid,row in sorted(ratings.items()))
    all_binary = tuple((mid,row['response']) for mid,row in sorted(onboarding.items()))
    effective_binary = tuple((mid,response) for mid,response in all_binary if mid not in ratings)
    result = CurrentInputs(RawRatingPacket(raw,ns,origin,version), BinaryPacket(effective_binary,ns,origin,version),
                           all_binary,tuple((mid,row['status']) for mid,row in sorted(watches.items())),len(raw)+len(effective_binary))
    validate_current(result, ns)
    return result


def call_raw_adapter(packet, adapter, expected_namespace):
    validate_packet(packet, RawRatingPacket)
    if packet.namespace != expected_namespace: raise ValueError('movie namespace mismatch')
    if packet.origin not in ('ACTUAL_FEELM','MOVIELENS_RAW_PROXY'): raise ValueError('origin cannot supply raw ratings')
    return adapter(packet)


def dispatch(inputs, raw_adapter, binary_adapter, expected_namespace):
    validate_current(inputs, expected_namespace)
    if any(status=='EXPIRED' for _,status in inputs.watch_states): return 'EXPIRED_POLICY_UNRESOLVED'
    if inputs.raw.observations and inputs.binary.observations: return 'UNRESOLVED_MIXED_RANKING'
    if inputs.raw.observations: return call_raw_adapter(inputs.raw,raw_adapter,expected_namespace)
    if inputs.binary.observations:
        return 'UNRESOLVED_BINARY_RANKING' if binary_adapter is None else binary_adapter(inputs.binary)
    return 'NO_INPUT'
