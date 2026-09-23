"""Shared persistence for cross-model critic feature/refactor notes."""
import copy
import fcntl
import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parent
DEFAULT_DATA_DIR = ROOT / 'data' / 'critic'
SCHEMA_VERSION = 1
AUTHORS = {'claude', 'codex'}
_SUMMARY_CACHE = {}
REFACTOR_REASONS = {
    'performance',
    'duplication',
    'maintainability',
    'reliability',
    'architecture',
    'security',
    'accessibility',
    'other',
}
FINDING_SEVERITIES = {'high', 'medium', 'low'}
MERGED_FEATURE_ITEMS = (
    ('duplicates', 'with'),
    ('minimal_refactors', 'title'),
    ('major_refactors', 'title'),
)


def critic_notes_path(target_dir, data_dir=None):
    target = os.path.realpath(str(target_dir))
    digest = hashlib.sha256(target.encode('utf-8')).hexdigest()[:20]
    return Path(data_dir or DEFAULT_DATA_DIR) / f'{digest}.json'


def _extract_object(raw):
    text = str(raw or '').strip()
    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char != '{':
            continue
        try:
            value, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and isinstance(value.get('features'), list):
            return value
    raise ValueError('critic output contains no valid feature JSON object')


def _validate_review_object(value):
    if not isinstance(value.get('summary'), str) or not value['summary'].strip():
        raise ValueError('critic output missing summary')
    if not isinstance(value.get('correctness_findings'), list):
        raise ValueError('critic output missing correctness_findings list')
    features = value.get('features')
    if not isinstance(features, list) or not features:
        raise ValueError('critic output missing feature coverage')
    required = {
        'name', 'category', 'subcategory', 'purpose', 'paths', 'duplicates',
        'minimal_refactors', 'major_refactors',
    }
    for feature in features:
        if not isinstance(feature, dict) or not required.issubset(feature):
            raise ValueError('critic output contains incomplete feature coverage')
        if not all(isinstance(feature[field], list) for field in (
            'paths', 'duplicates', 'minimal_refactors', 'major_refactors'
        )):
            raise ValueError('critic output contains invalid feature lists')


def _clean_text(value, fallback='', limit=1000):
    text = ' '.join(str(value or '').split())
    return (text or fallback)[:limit]


def clean_repo_paths(values):
    paths = []
    for value in values if isinstance(values, list) else []:
        path = str(value or '').replace('\\', '/').strip()
        while path.startswith('./'):
            path = path[2:]
        if (
            not path
            or path.startswith('/')
            or '..' in PurePosixPath(path).parts
            or path in paths
        ):
            continue
        paths.append(path[:500])
    return paths


def _normalize_suggestions(values, feature_paths):
    suggestions = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict):
            continue
        title = _clean_text(value.get('title'), limit=200)
        why = _clean_text(value.get('why'), limit=1200)
        if not title or not why:
            continue
        reason_category = _clean_text(
            value.get('reason_category'), fallback='maintainability', limit=40
        ).lower()
        if reason_category == 'speed':
            reason_category = 'performance'
        if reason_category not in REFACTOR_REASONS:
            reason_category = 'other'
        suggestions.append({
            'title': title,
            'why': why,
            'reason_category': reason_category,
            'paths': clean_repo_paths(value.get('paths')) or list(feature_paths),
        })
    return suggestions


def _normalize_duplicates(values):
    duplicates = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict):
            continue
        related = _clean_text(value.get('with'), limit=200)
        why = _clean_text(value.get('why'), limit=1200)
        if not related or not why:
            continue
        duplicates.append({
            'with': related,
            'why': why,
            'paths': clean_repo_paths(value.get('paths')),
        })
    return duplicates


def _normalize_feature(value):
    if not isinstance(value, dict):
        return None
    paths = clean_repo_paths(value.get('paths'))
    return {
        'name': _clean_text(value.get('name'), fallback='Unnamed feature', limit=200),
        'category': _clean_text(value.get('category'), fallback='uncategorized', limit=120),
        'subcategory': _clean_text(value.get('subcategory'), fallback='general', limit=120),
        'purpose': _clean_text(
            value.get('purpose'), fallback='Purpose not documented by critic.', limit=1500
        ),
        'paths': paths,
        'duplicates': _normalize_duplicates(value.get('duplicates')),
        'minimal_refactors': _normalize_suggestions(
            value.get('minimal_refactors'), paths
        ),
        'major_refactors': _normalize_suggestions(value.get('major_refactors'), paths),
    }


def _normalize_findings(values):
    findings = []
    for value in values if isinstance(values, list) else []:
        if not isinstance(value, dict):
            continue
        title = _clean_text(value.get('title'), limit=200)
        why = _clean_text(value.get('why'), limit=1200)
        if title and why:
            severity = _clean_text(
                value.get('severity'), fallback='medium', limit=20
            ).lower()
            if severity not in FINDING_SEVERITIES:
                severity = 'medium'
            findings.append({
                'severity': severity,
                'title': title,
                'why': why,
                'paths': clean_repo_paths(value.get('paths')),
            })
    return findings


def load_critic_notes(target_dir, data_dir=None):
    path = critic_notes_path(target_dir, data_dir=data_dir)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {
            'version': SCHEMA_VERSION,
            'target_dir': os.path.realpath(str(target_dir)),
            'providers': {},
        }
    return value if isinstance(value, dict) else {'providers': {}}


def _atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8', dir=path.parent, delete=False
    ) as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.write('\n')
        temp_path = Path(handle.name)
    os.replace(temp_path, path)


def path_matches_scope(path, scope):
    if str(scope or '').rstrip('/') in ('', '.'):
        return True
    return path == scope or path.startswith(scope.rstrip('/') + '/')


def _feature_key(feature):
    return tuple(
        str(feature.get(field, '')).casefold()
        for field in ('category', 'subcategory', 'name')
    )


def _item_paths(item, feature_paths):
    return item.get('paths') or feature_paths


def _merge_items(previous, current, previous_paths, reviewed_paths, identity_field):
    preserved = []
    for item in previous:
        item_paths = _item_paths(item, previous_paths)
        if not item_paths:
            preserved.append(item)
            continue
        remaining_paths = [
            path for path in item_paths
            if not any(path_matches_scope(path, scope) for scope in reviewed_paths)
        ]
        if not remaining_paths:
            continue
        kept = dict(item)
        kept['paths'] = remaining_paths
        preserved.append(kept)
    merged = []
    for item in preserved + current:
        identity = str(item.get(identity_field, '')).casefold()
        item_paths = item.get('paths') or []
        overlap_index = next((
            index for index, existing in enumerate(merged)
            if str(existing.get(identity_field, '')).casefold() == identity
            and (
                not existing.get('paths')
                or not item_paths
                or set(existing['paths']) & set(item_paths)
            )
        ), None)
        if overlap_index is None:
            merged.append(item)
            continue
        combined = dict(merged[overlap_index])
        combined.update(item)
        combined['paths'] = list(dict.fromkeys(
            (merged[overlap_index].get('paths') or []) + item_paths
        ))
        merged[overlap_index] = combined
    return merged


def _merge_scoped_features(previous, current, reviewed_paths):
    current_by_key = {}
    for feature in current:
        key = _feature_key(feature)
        existing = current_by_key.get(key)
        if existing is None:
            current_by_key[key] = feature
            continue
        combined = dict(existing)
        combined['paths'] = list(dict.fromkeys(
            existing.get('paths', []) + feature.get('paths', [])
        ))
        for field, identity in MERGED_FEATURE_ITEMS:
            combined[field] = _merge_items(
                existing.get(field, []), feature.get(field, []),
                existing.get('paths', []), [], identity,
            )
        current_by_key[key] = combined
    merged = []
    for old in previous:
        new = current_by_key.pop(_feature_key(old), None)
        old_paths = old.get('paths', [])
        remaining_old_paths = [
            path for path in old_paths
            if not any(path_matches_scope(path, scope) for scope in reviewed_paths)
        ]
        if new is None:
            if old_paths and not remaining_old_paths:
                continue
            kept = dict(old)
            if old_paths:
                kept['paths'] = remaining_old_paths
            for field, identity in MERGED_FEATURE_ITEMS:
                kept[field] = _merge_items(
                    old.get(field, []), [], old.get('paths', []),
                    reviewed_paths, identity,
                )
            merged.append(kept)
            continue

        combined = dict(new)
        combined['paths'] = list(dict.fromkeys(
            new.get('paths', []) + remaining_old_paths
        ))
        for field, identity in MERGED_FEATURE_ITEMS:
            combined[field] = _merge_items(
                old.get(field, []), new.get(field, []), old.get('paths', []),
                reviewed_paths, identity,
            )
        merged.append(combined)
    merged.extend(current_by_key.values())
    return merged


def record_review(target_dir, author, critic, raw_output, scope='', data_dir=None,
                  reviewed_paths=None):
    author = str(author or '').strip().lower()
    if author not in AUTHORS:
        raise ValueError(f'unsupported author: {author}')
    parsed = _extract_object(raw_output)
    _validate_review_object(parsed)
    features = []
    for value in parsed.get('features', []):
        feature = _normalize_feature(value)
        if feature:
            features.append(feature)
    review = {
        'critic': _clean_text(critic, limit=80),
        'reviewed_at': datetime.now(timezone.utc).isoformat(),
        'scope': _clean_text(scope, limit=500),
        'summary': _clean_text(parsed.get('summary'), limit=1500),
        'correctness_findings': _normalize_findings(parsed.get('correctness_findings')),
        'features': features,
    }
    had_reviewed_paths = bool(reviewed_paths)
    reviewed_paths = clean_repo_paths(reviewed_paths)
    if had_reviewed_paths and not reviewed_paths:
        raise ValueError('reviewed paths contain no safe repo-relative paths')

    path = critic_notes_path(target_dir, data_dir=data_dir)
    lock_path = path.with_suffix('.lock')
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open('a+', encoding='utf-8') as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        data = load_critic_notes(target_dir, data_dir=data_dir)
        data.update({
            'version': SCHEMA_VERSION,
            'target_dir': os.path.realpath(str(target_dir)),
            'updated_at': review['reviewed_at'],
        })
        providers = data.setdefault('providers', {})
        previous = providers.get(author, {})
        if reviewed_paths:
            review['correctness_findings'] = _merge_items(
                previous.get('correctness_findings', []),
                review['correctness_findings'], [], reviewed_paths, 'title',
            )
            review['features'] = _merge_scoped_features(
                previous.get('features', []), review['features'], reviewed_paths
            )
        providers[author] = review
        _atomic_write(path, data)
        _SUMMARY_CACHE.pop(str(path), None)
    return review


def scope_for_path(path):
    parts = PurePosixPath(path).parts
    return parts[0] if len(parts) > 1 else '(root)'


def critic_summary(target_dir, data_dir=None):
    path = critic_notes_path(target_dir, data_dir=data_dir)
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
    except OSError:
        stamp = None
    cached = _SUMMARY_CACHE.get(str(path))
    if cached and cached[0] == stamp:
        return copy.deepcopy(cached[1])
    data = load_critic_notes(target_dir, data_dir=data_dir)
    scopes = {}
    seen = {'minimal_refactors': set(), 'major_refactors': set()}
    totals = {'minimal_refactors': 0, 'major_refactors': 0}
    for review in (data.get('providers') or {}).values():
        for feature in review.get('features', []):
            feature_paths = feature.get('paths') or []
            for kind in ('minimal_refactors', 'major_refactors'):
                for suggestion in feature.get(kind, []):
                    paths = suggestion.get('paths') or feature_paths
                    key = (suggestion.get('title', '').casefold(), tuple(sorted(paths)))
                    if key in seen[kind]:
                        continue
                    seen[kind].add(key)
                    totals[kind] += 1
                    impacted = {scope_for_path(path) for path in paths}
                    if not impacted:
                        impacted = {feature.get('category') or 'uncategorized'}
                    for scope_name in impacted:
                        scope = scopes.setdefault(scope_name, {
                            'minimal_refactors': 0,
                            'major_refactors': 0,
                        })
                        scope[kind] += 1
    summary = {
        'available': bool(data.get('providers')),
        'minimal_refactors': totals['minimal_refactors'],
        'major_refactors': totals['major_refactors'],
        'scopes': scopes,
        'providers': sorted((data.get('providers') or {}).keys()),
    }
    _SUMMARY_CACHE[str(path)] = (stamp, summary)
    return copy.deepcopy(summary)
