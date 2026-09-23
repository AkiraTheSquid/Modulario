#!/usr/bin/env python3
"""Run reciprocal cross-model critic and persist normalized Modulario notes."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from critic_store import (  # noqa: E402
    clean_repo_paths,
    path_matches_scope,
    record_review,
)


MAX_DIFF_CHARS = 600_000
MAX_UNTRACKED_FILE_CHARS = 100_000
CRITIC_ACTIVE_ENV = 'MODULARIO_CRITIC_ACTIVE'
REVIEWERS = {
    'claude': ('codex', 'openai'),
    'codex': ('fable', 'anthropic'),
}
REVIEW_TIMEOUT_SECONDS = 300
AUTHOR_FAMILIES = {
    'claude': 'anthropic',
    'codex': 'openai',
}


def _git(repo, *args):
    result = subprocess.run(
        ['git', '-C', str(repo), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stdout, result.stderr.strip()


def _repo_root():
    code, output, _error = _git(Path.cwd(), 'rev-parse', '--show-toplevel')
    return Path(output.strip()).resolve() if code == 0 and output.strip() else None


def _selected_untracked(repo, requested_paths=None):
    _code, output, _error = _git(repo, 'ls-files', '--others', '--exclude-standard')
    paths = [line for line in output.splitlines() if line.strip()]
    if not requested_paths:
        return paths
    prefixes = clean_repo_paths(requested_paths)
    return [
        path for path in paths
        if any(path_matches_scope(path, prefix) for prefix in prefixes)
    ]


def normalize_requested_paths(repo, values):
    normalized = []
    for value in values:
        candidate = Path(value)
        resolved = candidate.resolve() if candidate.is_absolute() else (repo / candidate).resolve()
        try:
            relative = resolved.relative_to(repo)
        except ValueError as exc:
            raise ValueError(f'path outside repo: {value}') from exc
        path = relative.as_posix()
        if path and path not in normalized:
            normalized.append(path)
    return normalized


def _untracked_patch(repo, paths):
    chunks = []
    for rel_path in paths:
        path = (repo / rel_path).resolve()
        try:
            path.relative_to(repo)
            with path.open('rb') as handle:
                raw = handle.read(MAX_UNTRACKED_FILE_CHARS + 1)
        except (OSError, ValueError):
            continue
        if b'\0' in raw:
            chunks.append(f"diff --git a/{rel_path} b/{rel_path}\nnew binary file\n")
            continue
        truncated = len(raw) > MAX_UNTRACKED_FILE_CHARS
        if truncated:
            raise ValueError(
                f'untracked file exceeds critic limit: {rel_path}'
            )
        text = raw[:MAX_UNTRACKED_FILE_CHARS].decode('utf-8', 'replace')
        added = ''.join(f'+{line}\n' for line in text.splitlines())
        chunks.append(
            f"diff --git a/{rel_path} b/{rel_path}\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            f"+++ b/{rel_path}\n{added}"
        )
    return '\n'.join(chunks)


def collect_diff(repo, args):
    requested_paths = None
    if args and args[0] == '--files':
        requested_paths = args[1:]
        if not requested_paths:
            raise ValueError('--files requires at least one path')
        code, diff, error = _git(repo, 'diff', 'HEAD', '--', *requested_paths)
        scope = 'working diff of: ' + ' '.join(requested_paths)
    elif args:
        if len(args) != 1:
            raise ValueError('expected one base revision or --files paths')
        code, diff, error = _git(repo, 'diff', args[0])
        scope = f'tree vs {args[0]}'
    else:
        code, diff, error = _git(repo, 'diff', 'HEAD')
        scope = 'staged, unstaged, and untracked vs HEAD'
    if code:
        raise RuntimeError(error or 'git diff failed')
    diff += '\n' + _untracked_patch(repo, _selected_untracked(repo, requested_paths))
    if len(diff) > MAX_DIFF_CHARS:
        raise ValueError('diff exceeds critic limit; review smaller file scopes')
    return diff.strip(), scope


def _prompt(author, critic):
    return f"""You are {critic}, independent critic for code authored by {author}.
Review only supplied diff. Never edit or read files.

Return ONLY one JSON object matching this schema:
{{
  "summary": "short verdict",
  "correctness_findings": [
    {{"severity":"high|medium|low", "title":"...", "why":"concrete failure", "paths":["..."]}}
  ],
  "features": [
    {{
      "name":"feature name",
      "category":"broad domain",
      "subcategory":"specific capability",
      "purpose":"what this feature and its changed files do",
      "paths":["repo/relative/path"],
      "duplicates":[{{"with":"other feature", "why":"specific duplicated behavior/code", "paths":["..."]}}],
      "minimal_refactors":[{{"title":"small local refactor", "why":"why needed now", "reason_category":"performance|duplication|maintainability|reliability|architecture|security|accessibility|other", "paths":["..."]}}],
      "major_refactors":[{{"title":"significant architectural refactor", "why":"why worth planning", "reason_category":"performance|duplication|maintainability|reliability|architecture|security|accessibility|other", "paths":["..."]}}]
    }}
  ]
}}

Rules:
- This is one terminal review pass. Do not invoke another critic, model, agent,
  review CLI, or subprocess. Your review output must never trigger reciprocal review.
- Cover every changed feature/file in features; document category, subcategory, purpose.
- Minimal refactor = small, safe, local, immediately actionable by author. Critic never fixes it.
- Major refactor = broad redesign, framework change, or cross-feature consolidation. Plan only.
- Record duplication only with concrete paths/behavior and explain why consolidation helps.
- Classify each refactor reason. Use performance for concrete speed/runtime-cost concerns.
- Correctness findings need concrete failure behavior. No style nits.
- Empty arrays are valid. Never invent suggestions to fill them.
"""


def _review_payload(prompt, diff):
    return prompt + '\n<diff>\n' + diff + '\n</diff>\n'


def critic_for(author):
    critic, critic_family = REVIEWERS[author]
    if critic_family == AUTHOR_FAMILIES[author]:
        raise RuntimeError(f'self-review blocked: {author} -> {critic}')
    return critic


def _review_env(author, critic):
    env = os.environ.copy()
    env.update({
        CRITIC_ACTIVE_ENV: '1',
        'MODULARIO_CRITIC_AUTHOR': author,
        'MODULARIO_CRITIC_REVIEWER': critic,
    })
    return env


def _run_codex(repo, payload, env):
    return subprocess.run(
        [
            'codex', 'exec', '--sandbox', 'read-only',
            # Pinned, not inherited from ~/.codex/config.toml: the critic is the
            # last reader before a commit or deploy, so it always runs the
            # strongest model at the highest effort the CLI accepts. The config
            # file was found at "medium" on 2026-09-07 while CLAUDE.md said
            # "high" — a silent downgrade nobody noticed for weeks.
            '-m', 'gpt-5.6-sol', '-c', 'model_reasoning_effort=xhigh',
            '--skip-git-repo-check', '-C', str(repo), '-',
        ],
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=REVIEW_TIMEOUT_SECONDS,
    )


def _run_fable(repo, payload, env):
    return subprocess.run(
        [
            'claude', '-p', '--model', 'fable', '--effort', 'max',
            '--dangerously-skip-permissions', '--no-session-persistence',
            '--tools', '',
        ],
        cwd=repo,
        input=payload,
        capture_output=True,
        text=True,
        check=False,
        env=env,
        timeout=REVIEW_TIMEOUT_SECONDS,
    )


def main(argv=None):
    if os.environ.get(CRITIC_ACTIVE_ENV):
        print('critic: nested critic run blocked', file=sys.stderr)
        return 5
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] not in ('claude', 'codex'):
        print('usage: run_critic.py claude|codex [base|--files paths...]', file=sys.stderr)
        return 2
    author = args.pop(0)
    critic = critic_for(author)
    repo = _repo_root()
    if repo is None:
        print('critic: not inside a Git repo', file=sys.stderr)
        return 1
    try:
        if args and args[0] == '--files':
            args = ['--files', *normalize_requested_paths(repo, args[1:])]
        diff, scope = collect_diff(repo, args)
    except (ValueError, RuntimeError) as exc:
        print(f'critic: {exc}', file=sys.stderr)
        return 2
    if not diff:
        print(f'critic: empty diff ({scope})', file=sys.stderr)
        return 3

    print(f'# {critic} critic for {author} — {scope}', file=sys.stderr)
    prompt = _prompt(author, critic)
    payload = _review_payload(prompt, diff)
    env = _review_env(author, critic)
    try:
        with tempfile.TemporaryDirectory(prefix='modulario-critic-') as temp_dir:
            review_dir = Path(temp_dir)
            result = (
                _run_codex(review_dir, payload, env)
                if critic == 'codex'
                else _run_fable(review_dir, payload, env)
            )
    except subprocess.TimeoutExpired:
        print(
            f'critic: {critic} timed out after {REVIEW_TIMEOUT_SECONDS}s',
            file=sys.stderr,
        )
        return 6
    if result.returncode:
        print(result.stderr.strip() or f'{critic} critic failed', file=sys.stderr)
        return result.returncode
    output = result.stdout.strip()
    try:
        reviewed_paths = args[1:] if args and args[0] == '--files' else None
        review = record_review(
            repo, author, critic, output, scope=scope,
            reviewed_paths=reviewed_paths,
        )
    except ValueError as exc:
        print(f'critic notes not recorded: {exc}', file=sys.stderr)
        print(output)
        return 4
    quick = sum(len(feature['minimal_refactors']) for feature in review['features'])
    major = sum(len(feature['major_refactors']) for feature in review['features'])
    print(f'critic notes recorded: {quick} quick, {major} major', file=sys.stderr)
    print(output)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
