"""Movie/group geometry only: no predicted ratings or target labels."""
from collections import Counter

import numpy as np
from scipy import sparse
from scipy.stats import rankdata
from sklearn.preprocessing import normalize


def require(condition, message):
    if not condition:
        raise ValueError(message)


def id_rows(values):
    return [sorted({int(v) for v in row}) if row is not None else [] for row in values]


def block_matrix(rows, vocabulary, weights=None):
    lookup = {v: j for j, v in enumerate(vocabulary)}
    rr, cc = [], []
    for i, row in enumerate(rows):
        for value in row:
            if value in lookup:
                rr.append(i)
                cc.append(lookup[value])
    out = sparse.csr_matrix((np.ones(len(rr)), (rr, cc)),
                            shape=(len(rows), len(vocabulary)), dtype=np.float64)
    if weights is not None:
        out = out.multiply(weights).tocsr()
    return normalize(out, norm="l2", copy=False).tocsr() if all(out.shape) else out


def movie_vectors(genres, keywords, min_df=5):
    genres, keywords = id_rows(genres), id_rows(keywords)
    require(len(genres) == len(keywords), "same movie axis")
    gv = sorted({g for row in genres for g in row})
    df = Counter(k for row in keywords for k in row)
    kv = sorted(k for k, n in df.items() if n >= min_df)
    idf = np.array([np.log((1 + len(genres)) / (1 + df[k])) + 1 for k in kv])
    vocabulary = {"genre_ids": gv, "keyword_ids": kv, "keyword_idf": idf.tolist()}
    x, valid, coverage = transform_movies(genres, keywords, vocabulary)
    return x, valid, {**vocabulary, **coverage}


def transform_movies(genres, keywords, vocabulary):
    """Apply frozen axes/IDF; never learn from or mutate incoming movies."""
    genres, keywords = id_rows(genres), id_rows(keywords)
    require(len(genres) == len(keywords), "same movie axis")
    gv, kv = vocabulary["genre_ids"], vocabulary["keyword_ids"]
    idf = np.asarray(vocabulary["keyword_idf"], dtype=np.float64)
    require(len(idf) == len(kv), "frozen IDF axis")
    gm, km = block_matrix(genres, gv), block_matrix(keywords, kv, idf)
    blocks = (gm.getnnz(axis=1) > 0).astype(int) + (km.getnnz(axis=1) > 0)
    x = sparse.hstack([gm, km], format="csr")
    x = x.multiply(1 / np.sqrt(np.maximum(blocks, 1))[:, None]).tocsr()
    require(np.isfinite(x.data).all(), "finite features")
    norms = np.asarray(x.multiply(x).sum(axis=1)).ravel()
    require(np.allclose(norms[blocks > 0], 1, atol=1e-12), "unit movie vectors")
    known_g, known_k = set(gv), set(kv)
    return x, blocks > 0, {"block_counts": blocks.tolist(),
                          "oov_genres": [len(set(row) - known_g) for row in genres],
                          "oov_keywords": [len(set(row) - known_k) for row in keywords]}


def midrank(values):
    values = np.asarray(values, dtype=np.float64)
    require(len(values) > 0 and np.isfinite(values).all(), "finite nonempty ranks")
    return (rankdata(values, method="average") - .5) / len(values)


def reference_percentile(values, frozen_reference):
    """Query a fixed empirical CDF, including exact-tie midrank."""
    ref = np.sort(np.asarray(frozen_reference, dtype=np.float64))
    values = np.asarray(values, dtype=np.float64)
    require(len(ref) > 0 and np.isfinite(ref).all() and np.isfinite(values).all(), "finite reference")
    return (np.searchsorted(ref, values, side="left") + np.searchsorted(ref, values, side="right")) / (2 * len(ref))


def group_ranks(values, labels, k):
    result = np.empty(len(values), dtype=np.float64)
    for g in range(k):
        ix = np.flatnonzero(labels == g)
        result[ix] = midrank(values[ix])
    return result


def squared_distances(x, centers):
    norms = np.asarray(x.multiply(x).sum(axis=1)).ravel()
    d = norms[:, None] + np.sum(centers ** 2, axis=1)[None, :] - 2 * (x @ centers.T)
    require(np.isfinite(d).all() and np.min(d) >= -1e-10, "valid squared distance")
    return np.maximum(d, 0)


def canonical_model(x, centers, labels, movie_ids):
    k = len(centers)
    require(len(np.unique(labels)) == k, "no empty groups")
    order = sorted(range(k), key=lambda g: int(np.min(movie_ids[labels == g])))
    inverse = np.empty(k, dtype=int)
    inverse[order] = np.arange(k)
    centers = np.asarray(centers[order], dtype=np.float64)
    labels = inverse[labels]
    nearest = np.argmin(squared_distances(x, centers), axis=1)
    require(np.array_equal(nearest, labels), "canonical nearest assignment")
    means = np.vstack([np.asarray(x[labels == g].mean(axis=0)).ravel() for g in range(k)])
    require(np.allclose(centers, means, atol=1e-6, rtol=1e-6), "centers equal assigned means")
    return centers, labels


def geometry_values(x, centers, labels):
    d = squared_distances(x, centers)
    require(np.array_equal(np.argmin(d, axis=1), labels), "nearest group")
    norm = np.sum(centers ** 2, axis=1)
    sep = np.sqrt(np.maximum(norm[:, None] + norm[None, :] - 2 * centers @ centers.T, 0))
    off_diagonal = ~np.eye(len(centers), dtype=bool)
    require((sep[off_diagonal] > 1e-10).all(), "distinct centers")
    own = d[np.arange(x.shape[0]), labels]
    denominator = 2 * sep[labels, :]
    denominator[np.arange(x.shape[0]), labels] = np.inf
    boundary = (d - own[:, None]) / denominator
    boundary[np.arange(x.shape[0]), labels] = np.inf
    boundary = np.min(boundary, axis=1)
    require(np.isfinite(boundary).all() and np.min(boundary) >= -1e-10, "nonnegative boundary")
    boundary = np.maximum(boundary, 0)
    radius = np.sqrt(own)
    return radius, boundary


def geometry(x, centers, labels):
    radius, boundary = geometry_values(x, centers, labels)
    return radius, boundary, group_ranks(radius, labels, len(centers)), group_ranks(boundary, labels, len(centers))


def assign_frozen(x, valid, centers, reference_labels, reference_radius, reference_boundary):
    """Append-safe assignment: centers, group IDs and reference CDFs stay fixed."""
    n = x.shape[0]
    labels = np.full(n, -1, dtype=int)
    radius, boundary, rp, bp = [np.full(n, np.nan) for _ in range(4)]
    use = np.flatnonzero(valid)
    if len(use):
        labels[use] = np.argmin(squared_distances(x[use], centers), axis=1)
        radius[use], boundary[use] = geometry_values(x[use], centers, labels[use])
        for g in np.unique(labels[use]):
            qi = use[labels[use] == g]
            ri = np.flatnonzero(reference_labels == g)
            rp[qi] = reference_percentile(radius[qi], reference_radius[ri])
            bp[qi] = reference_percentile(boundary[qi], reference_boundary[ri])
    return labels, radius, boundary, rp, bp


def append_new_movies(movie_ids, genres, keywords, vocabulary, centers, reference, registered_ids):
    """Stateless append: caller must supply and atomically persist the current registry."""
    ids = np.asarray(movie_ids, dtype=np.int64)
    registered_ids = np.asarray(registered_ids, dtype=np.int64)
    require(len(ids) > 0 and len(np.unique(ids)) == len(ids) and (ids > 0).all(), "unique positive new IDs")
    require(len(np.unique(registered_ids)) == len(registered_ids), "unique current registry")
    require(np.isin(reference["catalog_movie_ids"], registered_ids).all(), "registry includes all initial IDs including unsupported")
    require(not np.isin(ids, registered_ids).any(), "existing movie update is not append")
    require(len(ids) == len(genres) == len(keywords), "new movie row axis")
    x, valid, coverage = transform_movies(genres, keywords, vocabulary)
    assigned = assign_frozen(x, valid, centers, reference["labels"], reference["radius"], reference["boundary"])
    return x, valid, assigned, coverage, np.concatenate([registered_ids, ids])


def anchor_distance(x, positives):
    require(positives.shape[0] > 0 and x.shape[1] == positives.shape[1], "positive anchors and same feature axis required")
    similarities = (x @ positives.T).toarray()
    return 1 - np.max(np.clip(similarities, 0, 1), axis=1)


def compare_user_groups(x, centers, labels, viewed, oi, stars, max_groups=5):
    viewed, oi = np.asarray(viewed, int), np.asarray(oi, int)
    stars = np.asarray(stars, float)
    require(len(oi) == len(stars), "input stars axis")
    require(np.isin(oi, viewed).all(), "inputs in known history")
    k = len(centers)
    usable_v = viewed[labels[viewed] >= 0]
    positives = oi[(stars >= 4) & (labels[oi] >= 0)]
    counts = np.bincount(labels[usable_v], minlength=k)
    affinity = np.full(k, np.nan)
    positive_groups = np.zeros(k, dtype=bool)
    if len(positives):
        norm = np.linalg.norm(centers, axis=1)
        require((norm > 0).all(), "nonzero centroid affinity")
        affinity = np.max(np.clip((x[positives] @ centers.T) / norm[None, :], 0, 1), axis=0)
        positive_groups[np.unique(labels[positives])] = True
    eligible_1 = (counts <= 1) & (affinity >= .25) & (len(usable_v) == len(viewed))
    eligible_2 = positive_groups & (affinity >= .25)
    ordered = np.lexsort((np.arange(k), counts, -np.nan_to_num(affinity, nan=-1)))
    selected_1 = ordered[eligible_1[ordered]][:max_groups]
    selected_2 = ordered[eligible_2[ordered]][:max_groups]
    return {"experience": counts, "affinity": affinity, "positive_group": positive_groups,
            "eligible_m1": eligible_1, "eligible_m2a": eligible_2,
            "selected_m1": selected_1, "selected_m2a": selected_2,
            "positives": positives, "missing_viewed_geometry": len(viewed) - len(usable_v)}
