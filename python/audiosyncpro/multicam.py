from __future__ import annotations

from collections import defaultdict
from typing import Dict, List, Tuple

import numpy as np

from .types import PairwiseResult


def build_weighted_graph(results: List[PairwiseResult]):
    """Return adjacency list and edge dict keyed by (i, j)."""
    adj = defaultdict(list)
    edges = {}
    for r in results:
        i, j = r.ref_index, r.target_index
        adj[i].append(j)
        edges[(i, j)] = r
    return adj, edges


def find_connected_components(n: int, adj: dict) -> List[List[int]]:
    visited = [False] * n
    components = []
    for i in range(n):
        if visited[i]:
            continue
        comp = []
        stack = [i]
        visited[i] = True
        while stack:
            v = stack.pop()
            comp.append(v)
            for u in adj[v]:
                if not visited[u]:
                    visited[u] = True
                    stack.append(u)
        components.append(comp)
    return components


def choose_reference(component: List[int], edges: dict, results: List[PairwiseResult]) -> int:
    """Choose the clip with highest sum of confidences and the longest analyzed signal."""
    best = component[0]
    best_score = -1.0
    for v in component:
        score = 0.0
        for u in component:
            if v == u:
                continue
            r = edges.get((v, u)) or edges.get((u, v))
            if r:
                # Use the signed offset direction; confidence is symmetric.
                score += r.confidence
        # mild tie-breaker: larger index? not available. Use id.
        if score > best_score:
            best_score = score
            best = v
    return best


def optimize_offsets(
    component: List[int],
    edges: dict,
    sample_rate: int,
    outlier_threshold_multiplier: float = 3.0,
) -> Tuple[Dict[int, float], List[Tuple[int, int]]]:
    """Solve weighted least-squares for offsets relative to the reference.

    Variables x_i are the start time of each clip on a common timeline.
    For edge (i, j): x_j - x_i ≈ o_ij.
    We fix the reference at 0 and solve the overdetermined system.
    Returns {clip_index: offset_seconds} and list of rejected edge tuples.
    """
    ref = choose_reference(component, edges, [])
    var_index = {idx: i for i, idx in enumerate(component)}
    n = len(component)
    if n == 1:
        return {component[0]: 0.0}, []

    # Build matrix and vector.
    rows = []
    rhs = []
    weights = []
    for i in component:
        for j in component:
            if i == j:
                continue
            r = edges.get((i, j))
            if not r:
                continue
            row = [0.0] * n
            row[var_index[j]] = 1.0
            row[var_index[i]] = -1.0
            rows.append(row)
            rhs.append(r.offset_seconds)
            weights.append(r.confidence)

    if not rows:
        return {idx: 0.0 for idx in component}, []

    A = np.array(rows, dtype=np.float64)
    b = np.array(rhs, dtype=np.float64)
    w = np.array(weights, dtype=np.float64)
    w[w < 1e-6] = 1e-6
    W = np.diag(w)

    # Fix reference by removing its column and treating x_ref = 0.
    ref_col = var_index[ref]
    mask = [c != ref_col for c in range(n)]
    A_red = A[:, mask]
    # reference contributes -A[:, ref_col] * 0, so nothing to subtract.
    Aw = A_red.T @ W
    try:
        x_red, _, _, _ = np.linalg.lstsq(Aw @ A_red, Aw @ b, rcond=None)
    except Exception:
        # Fallback to per-edge reference offsets.
        offsets = {ref: 0.0}
        for idx in component:
            if idx == ref:
                continue
            r = edges.get((ref, idx)) or edges.get((idx, ref))
            offsets[idx] = r.offset_seconds if r else 0.0
        return offsets, []

    offsets = {ref: 0.0}
    idx_list = [idx for idx in component]
    for idx, val in zip([c for c in component if c != ref], x_red):
        offsets[idx] = float(val)

    # Recompute residuals and reject outliers.
    residuals = []
    for row_i, row in enumerate(A):
        i_idx = component[int(np.where(np.array(row[:n]) == -1.0)[0][0])] if True else 0
        # easier: use edges list.
    # Build edge list for residuals.
    edge_list = []
    for i in component:
        for j in component:
            if i == j:
                continue
            r = edges.get((i, j))
            if r:
                edge_list.append((i, j, r))

    residuals = []
    for i, j, r in edge_list:
        pred = offsets[j] - offsets[i]
        residuals.append(abs(pred - r.offset_seconds))
    if not residuals:
        return offsets, []

    median_res = float(np.median(residuals))
    threshold = max(0.01, outlier_threshold_multiplier * (median_res + 0.001))
    rejected = []
    for (i, j, r), res in zip(edge_list, residuals):
        if res > threshold:
            rejected.append((i, j))

    if rejected:
        # Re-solve without rejected edges.
        rows2 = []
        rhs2 = []
        weights2 = []
        for i, j, r in edge_list:
            if (i, j) in rejected:
                continue
            row = [0.0] * n
            row[var_index[j]] = 1.0
            row[var_index[i]] = -1.0
            rows2.append(row)
            rhs2.append(r.offset_seconds)
            weights2.append(r.confidence)
        if rows2:
            A2 = np.array(rows2, dtype=np.float64)[:, mask]
            b2 = np.array(rhs2, dtype=np.float64)
            w2 = np.array(weights2, dtype=np.float64)
            w2[w2 < 1e-6] = 1e-6
            W2 = np.diag(w2)
            try:
                x_red2, _, _, _ = np.linalg.lstsq(A2.T @ W2 @ A2, A2.T @ W2 @ b2, rcond=None)
                offsets = {ref: 0.0}
                for idx, val in zip([c for c in component if c != ref], x_red2):
                    offsets[idx] = float(val)
            except Exception:
                pass

    return offsets, rejected
