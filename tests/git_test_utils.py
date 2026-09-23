import subprocess


def init_git_repo(repo, files):
    subprocess.run(['git', 'init', '-q', str(repo)], check=True)
    subprocess.run(
        ['git', '-C', str(repo), 'config', 'user.email', 'test@example.com'],
        check=True,
    )
    subprocess.run(
        ['git', '-C', str(repo), 'config', 'user.name', 'Test'],
        check=True,
    )
    for rel_path, contents in files.items():
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents, encoding='utf-8')
    subprocess.run(['git', '-C', str(repo), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(repo), 'commit', '-qm', 'init'], check=True)
