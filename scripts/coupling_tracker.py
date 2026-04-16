"""
Modulario — change coupling detection.

Reads history.jsonl, enumerates all file pairs that co-changed in the same
session, computes coupling strength, and writes _coupling.json.

Always accumulates data (even with few sessions) so the signal can be
inspected as it grows.  Coupling *warnings* are gated in build_claude_summary
by coupling_min_sessions so they only fire when the sample is meaningful.
"""
import json
import os
from itertools import combinations
from datetime import datetime


# ─── Path helper ──────────────────────────────────────────────────────────────

def coupling_path(output_path):
    """Return _coupling.json path for a given state.json path."""
    stem = output_path[:-5] if output_path.endswith('.json') else output_path
    return stem + '_coupling.json'


# ─── Core computation ─────────────────────────────────────────────────────────

def update_coupling(coup_path, history, thresholds):
    """
    Enumerate co-change pairs from *history* within coupling_window sessions.
    Always writes coupling.json (even below min_sessions).
    Returns coupling data dict.
    """
    window_size = thresholds.get('coupling_window', 30)
    ct          = thresholds.get('coupling_thresholds',
                                 {'moderate': 0.3, 'strong': 0.5, 'critical': 0.7})
    min_count   = thresholds.get('coupling_min_count', 5)

    window        = history[-window_size:] if len(history) > window_size else history
    session_count = len(window)

    # Per-file session counts derived from the history window.
    sessions_map = {}
    for record in window:
        for path in record.get('changed', []):
            sessions_map[path] = sessions_map.get(path, 0) + 1

    # Enumerate all pairs that co-occurred in at least one session
    co_count = {}
    for record in window:
        changed = record.get('changed', [])
        if len(changed) < 2:
            continue
        for a, b in combinations(sorted(changed), 2):
            co_count[(a, b)] = co_count.get((a, b), 0) + 1

    # Compute strength; keep pairs at MODERATE+
    pairs = []
    for (a, b), count in co_count.items():
        sa     = sessions_map.get(a, 0)
        sb     = sessions_map.get(b, 0)
        denom  = min(sa, sb) if min(sa, sb) > 0 else 1
        strength = count / denom

        if strength < ct.get('moderate', 0.3):
            continue

        if strength >= ct.get('critical', 0.7) and count >= min_count:
            status = 'CRITICAL'
        elif strength >= ct.get('strong', 0.5):
            status = 'STRONG'
        else:
            status = 'MODERATE'

        pairs.append({
            'file_a':           a,
            'file_b':           b,
            'co_change_count':  count,
            'sessions_a':       sa,
            'sessions_b':       sb,
            'coupling_strength': round(strength, 3),
            'coupling_status':  status,
        })

    pairs.sort(key=lambda p: (-p['coupling_strength'], -p['co_change_count']))

    coupling_data = {
        'session_count': session_count,
        'window_size':   window_size,
        'last_updated':  datetime.now().isoformat(),
        'pairs':         pairs,
    }
    try:
        with open(coup_path, 'w', encoding='utf-8') as f:
            json.dump(coupling_data, f, indent=2)
    except OSError:
        pass

    return coupling_data


# ─── Query helpers ────────────────────────────────────────────────────────────

def find_strong_pairs(coupling_data, file_path):
    """Return STRONG/CRITICAL pairs involving file_path, ranked by strength."""
    result = []
    for p in coupling_data.get('pairs', []):
        if p['coupling_status'] in ('STRONG', 'CRITICAL'):
            if p['file_a'] == file_path or p['file_b'] == file_path:
                result.append(p)
    return result


# ─── Interpretation heuristics ────────────────────────────────────────────────

_PY_EXTS  = {'.py'}
_JS_EXTS  = {'.js', '.ts', '.jsx', '.tsx', '.mjs', '.cjs'}
_LAYER_DIRS = {
    'frontend', 'backend', 'client', 'server', 'api', 'web', 'app',
    'ui', 'tui', 'cli', 'lib', 'core', 'utils', 'utilities', 'scripts',
    'components', 'pages', 'views', 'models', 'routes', 'handlers',
}


def _is_test_file(name):
    stem = os.path.splitext(name)[0].lower()
    return (
        stem.startswith('test_') or stem.endswith('_test') or
        '.test.' in name.lower() or '.spec.' in name.lower()
    )


def interpret_coupling(path_a, path_b):
    """
    Return (kind, explanation) using structural heuristics.

    kind is one of: 'test-pair' | 'cross-layer' | 'same-dir' | 'unknown'

    explanation is a human-readable string suitable for inclusion in the
    PostToolUse [Modulario] block.
    """
    name_a = os.path.basename(path_a)
    name_b = os.path.basename(path_b)
    ext_a  = os.path.splitext(name_a)[1].lower()
    ext_b  = os.path.splitext(name_b)[1].lower()
    dir_a  = os.path.dirname(path_a)
    dir_b  = os.path.dirname(path_b)

    # Test ↔ implementation
    if _is_test_file(name_a) or _is_test_file(name_b):
        return (
            'test-pair',
            "One file appears to be a test. Test ↔ implementation co-change is "
            "expected and healthy — this is not a structural concern.",
        )

    # Cross-language (Python ↔ JS/TS)
    a_py = ext_a in _PY_EXTS;  b_py = ext_b in _PY_EXTS
    a_js = ext_a in _JS_EXTS;  b_js = ext_b in _JS_EXTS
    if (a_py and b_js) or (b_py and a_js):
        return (
            'cross-layer',
            "These files use different languages (Python / JS-family), suggesting a "
            "frontend–backend boundary. Co-changing across this boundary is normal for "
            "full-stack feature work — likely task-scope coupling, not a hidden structural "
            "dependency.",
        )

    # Same directory
    if dir_a == dir_b:
        label = dir_a or 'the project root'
        return (
            'same-dir',
            f"Both files live in {label}/. Files that always co-change in the same "
            "directory often represent one logical concept split across two files. "
            "Consider: should they be merged? Or should a shared interface be extracted "
            "to make the dependency explicit?",
        )

    # Cross-directory, same language — check for layer directories
    top_a = path_a.split('/')[0] if '/' in path_a else dir_a
    top_b = path_b.split('/')[0] if '/' in path_b else dir_b
    if top_a.lower() in _LAYER_DIRS or top_b.lower() in _LAYER_DIRS:
        return (
            'cross-layer',
            f"These files sit in different top-level directories ({top_a}/ and {top_b}/). "
            "Cross-directory co-change often reflects task scope — a feature touching "
            "multiple layers — rather than a hidden dependency. Worth checking whether an "
            "implicit contract (shared data format, config key, event name) ties them "
            "together without any import relationship.",
        )

    return (
        'unknown',
        "The structural relationship between these files is not obvious from their paths. "
        "Check whether they share an implicit contract (a data format, naming convention, "
        "or config key) that no import captures. Co-change without an import relationship "
        "is the most actionable form of hidden coupling.",
    )
