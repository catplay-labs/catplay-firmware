#!/usr/bin/env python3
"""Prepare release metadata or publish explicit revisions as remote Git tags."""
import argparse
import base64
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime, timezone
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlsplit

VERSION_TAG_PATTERN = re.compile(r"^v0\.(\d+)\.(\d+)(?:-pre(\d+))?$")
COMMIT_PATTERN = re.compile(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})\Z")


@dataclass
class Repository:
    display_name: str
    url: str
    branch: str
    checkout_dir: Path | None
    revision: str | None
    submodules: bool = True
    use_existing_checkout: bool = False


def repository_url(value):
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", value) and not Path(value).exists():
        return "https://github.com/" + value
    if value.startswith(("https://", "http://", "ssh://", "file://")):
        parsed = urlsplit(value)
        if parsed.scheme in ("http", "https") and (parsed.username or parsed.password):
            raise argparse.ArgumentTypeError("pass credentials with --token or --token-file, not in the URL")
        return value
    if re.match(r"[^/@:]+@[^/:]+:.+", value):
        return value
    if Path(value).exists():
        return str(Path(value).resolve())
    raise argparse.ArgumentTypeError("expected repository URL, local path or GitHub OWNER/REPO")


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    for name in ("catplay", "firmware"):
        common.add_argument(f"--{name}-repo", type=repository_url, required=True)
        common.add_argument(f"--{name}-branch", default="master")
        common.add_argument(f"--{name}-checkout", type=Path, help="checkout directory; optional object cache for push-tags")
        common.add_argument(f"--{name}-display-name", default="Catplay" if name == "catplay" else "Firmware")
        common.add_argument(f"--{name}-revision", help="exact revision or remote ref; required for push-tags")
        common.add_argument(f"--{name}-skip-submodules", action="store_true")
        common.add_argument(f"--{name}-use-existing-checkout", action="store_true",
                            help="verify the supplied SHA and read the existing checkout without modifying it")
        common.add_argument(f"--{name}-token-file", type=Path,
                            help="override the shared token for this repository")
    auth = common.add_mutually_exclusive_group()
    auth.add_argument("--token", help="HTTP Git access token; prefer --token-file")
    auth.add_argument("--token-file", type=Path, help="HTTP Git token file, or /dev/stdin")
    commands = parser.add_subparsers(dest="command", required=True)
    checkout = commands.add_parser("checkout", parents=[common], help="checkout sources and generate release metadata")
    checkout.add_argument("--release-notes", type=Path, required=True)
    checkout.add_argument("--tag-file", type=Path, required=True)
    checkout.add_argument("--tag", help="explicit tag instead of automatic version selection")
    checkout.add_argument("--tag-prerelease", action="store_true", help="generate a -preN tag; independent of GitHub release status")
    commands.add_parser("has-changes", parents=[common], help="exit 1 when neither checkout has new commits")
    push = commands.add_parser("push-tags", parents=[common], help="tag explicit revisions; checkouts are optional caches")
    push.add_argument("--tag", required=True)
    for name in ("catplay", "firmware"):
        push.add_argument(f"--skip-{name}-tag", action="store_true",
                          help=f"do not access or tag the {name} repository")
    push.add_argument("--tagger-name", help="annotated tag author; otherwise use existing Git identity")
    push.add_argument("--tagger-email", help="annotated tag author email; required with --tagger-name")
    args = parser.parse_args(argv)
    if bool(getattr(args, "tagger_name", None)) != bool(getattr(args, "tagger_email", None)):
        parser.error("--tagger-name and --tagger-email must be provided together")
    for name in ("catplay", "firmware"):
        if args.command != "push-tags" and getattr(args, name + "_checkout") is None:
            parser.error(f"--{name}-checkout is required for {args.command}")
        revision = getattr(args, name + "_revision", None)
        if getattr(args, name + "_use_existing_checkout") and (revision is None or not COMMIT_PATTERN.fullmatch(revision)):
            parser.error(f"--{name}-use-existing-checkout requires a full --{name}-revision SHA")
        if args.command == "push-tags" and not getattr(args, "skip_" + name + "_tag") and revision is None:
            parser.error(f"--{name}-revision is required for push-tags")
        if revision is not None and (not revision.strip() or revision.startswith("-") or ":" in revision):
            parser.error(f"invalid --{name}-revision")
    if getattr(args, "tag", None) is not None:
        args.tag = args.tag.strip()
        if not args.tag:
            parser.error("--tag must not be empty")
    if args.command == "push-tags" and args.skip_catplay_tag and args.skip_firmware_tag:
        parser.error("select at least one repository to tag")
    return args


class Git:
    def __init__(self, repositories, token=None, tagger_name=None, tagger_email=None, repository_tokens=None):
        # Environment inheritance is for Git/SSH execution only. Release
        # configuration and credentials are provided by main() arguments.
        self.environment = os.environ.copy()
        self.environment["GIT_TERMINAL_PROMPT"] = "0"
        self.repository_tokens = repository_tokens or {}
        self.tagger_name, self.tagger_email = tagger_name, tagger_email
        if tagger_name is not None:
            self.environment["GIT_COMMITTER_NAME"] = tagger_name
            self.environment["GIT_COMMITTER_EMAIL"] = tagger_email
        self.secrets = []
        if token is not None:
            if not token.strip():
                raise ValueError("Git token is empty")
            token = token.strip()
            encoded = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            self.secrets = [token, quote(token, safe=""), encoded]
            count = int(self.environment.get("GIT_CONFIG_COUNT", "0"))
            hosts = sorted({f"{urlsplit(repo.url).scheme}://{urlsplit(repo.url).netloc}/"
                            for repo in repositories if repo.url.startswith(("https://", "http://"))})
            for host in hosts:
                # Clear a cached checkout's Actions header before installing our
                # token. Neither the token nor an authenticated URL is persisted.
                for value in ("", "Authorization: Basic " + encoded):
                    self.environment[f"GIT_CONFIG_KEY_{count}"] = f"http.{host}.extraHeader"
                    self.environment[f"GIT_CONFIG_VALUE_{count}"] = value
                    count += 1
            self.environment["GIT_CONFIG_COUNT"] = str(count)

    def for_repository(self, repository):
        if repository.url not in self.repository_tokens:
            return self
        return Git([repository], self.repository_tokens[repository.url], self.tagger_name, self.tagger_email)

    def redact(self, text):
        for secret in self.secrets:
            text = text.replace(secret, "[MASKED]")
        return text

    def call(self, *args, cwd=None, check=True, input=None):
        print(self.redact("+ git " + " ".join(map(str, args)) + (f" (cwd={cwd})" if cwd else "")), flush=True)
        result = subprocess.run(["git", *map(str, args)], cwd=cwd, env=self.environment,
                                text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, input=input)
        if check and result.returncode:
            raise RuntimeError(self.redact(f"git {args[0]} failed ({result.returncode}): " + result.stderr.strip()))
        return result

    def capture(self, *args, cwd=None):
        return self.call(*args, cwd=cwd).stdout.strip()


def checkout_repo(repository, git):
    directory = repository.checkout_dir
    if repository.use_existing_checkout:
        if not (directory / ".git").exists():
            raise ValueError(f"{directory} is not an existing checkout")
        commit = git.capture("rev-parse", "--verify", "HEAD^{commit}", cwd=directory)
        if commit != repository.revision.lower():
            raise ValueError(f"{repository.display_name}: checkout HEAD is {commit}, expected {repository.revision}")
        return
    git = git.for_repository(repository)
    if repository.revision is None:
        git.call("check-ref-format", "--branch", repository.branch)
    directory.parent.mkdir(parents=True, exist_ok=True)
    if (directory / ".git").exists():
        git.call("remote", "set-url", "origin", repository.url, cwd=directory)
    else:
        if directory.exists() and any(directory.iterdir()):
            raise ValueError(f"{directory} exists but is not a git checkout")
        git.call("clone", "--no-checkout", "--", repository.url, directory)
    git.call("fetch", "--tags", "--force", "origin", repository.revision or repository.branch, cwd=directory)
    commit = git.capture("rev-parse", "--verify", "FETCH_HEAD^{commit}", cwd=directory)
    if repository.revision is not None:
        git.call("checkout", "--force", "--detach", commit, cwd=directory)
    else:
        git.call("checkout", "--force", "-B", repository.branch, commit, cwd=directory)
    git.call("reset", "--hard", commit, cwd=directory)
    if repository.submodules and (directory / ".gitmodules").exists():
        git.call("submodule", "sync", "--recursive", cwd=directory)
        git.call("submodule", "update", "--init", "--recursive", cwd=directory)


def parse_version_tag(tag: str) -> tuple[int, int, int | None] | None:
    match = VERSION_TAG_PATTERN.fullmatch(tag)
    if not match:
        return None

    minor = int(match.group(1))
    patch = int(match.group(2))
    prerelease = match.group(3)
    return minor, patch, int(prerelease) if prerelease else None


def version_sort_key(tag: str) -> tuple[int, int, int, int]:
    parsed = parse_version_tag(tag)
    if parsed is None:
        raise ValueError(f"Invalid version tag: {tag}")

    minor, patch, prerelease = parsed
    if prerelease is None:
        return minor, patch, 1, 0
    return minor, patch, 0, prerelease


def matching_version_tags(repo_dir: Path, git) -> list[str]:
    tags = git.capture("tag", "--list", "v0.*", cwd=repo_dir)
    return [tag for tag in tags.splitlines() if parse_version_tag(tag)]


def latest_version_tag(repo_dir: Path, git) -> str | None:
    tags = matching_version_tags(repo_dir, git)
    return max(tags, key=version_sort_key) if tags else None


def next_version_tag(repo_dirs: list[Path], tag_prerelease: bool, git) -> str:
    tags = []
    for repo_dir in repo_dirs:
        tags.extend(matching_version_tags(repo_dir, git))

    latest_tag = max(tags, key=version_sort_key) if tags else None
    if latest_tag is None:
        return "v0.1.0-pre1" if tag_prerelease else "v0.1.0"

    minor, patch, latest_pre = parse_version_tag(latest_tag)
    if latest_pre is not None:
        if tag_prerelease:
            return f"v0.{minor}.{patch}-pre{latest_pre + 1}"
        return f"v0.{minor}.{patch}"

    if tag_prerelease:
        return f"v0.{minor + 1}.0-pre1"
    return f"v0.{minor + 1}.0"


def commit_lines(repository, previous_tag, git):
    revision_range = f"{previous_tag}..HEAD" if previous_tag else "HEAD"
    output = git.capture("log", "--no-merges", "--date=short",
                         "--pretty=format:%h%x1f%H%x1f%ad%x1f%s%x1f%an", revision_range,
                         cwd=repository.checkout_dir)
    lines = []
    for record in output.splitlines():
        short_hash, full_hash, date, subject, author = record.split("\x1f", 4)
        ref = f"`{short_hash}`"
        if repository.url.startswith(("https://", "http://")):
            ref = f"[`{short_hash}`]({repository.url.removesuffix('.git')}/commit/{full_hash})"
        lines.append(f"- {ref} {date} {subject} ({author})")
    return lines


def append_commit_section(lines, repository, previous_tag, git):
    lines.extend([f"## {repository.display_name} commits", "",
                  f"Previous release tag: `{previous_tag}`" if previous_tag else "Previous release tag: none found.", ""])
    lines.extend(commit_lines(repository, previous_tag, git) or ["No new commits."])
    lines.append("")


def prepare_checkout(repositories, git, *, release_notes, tag_file, tag_prerelease=False, tag=None):
    for repository in repositories:
        checkout_repo(repository, git)
    tag = tag or next_version_tag([repo.checkout_dir for repo in repositories], tag_prerelease, git)
    now = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    lines = [f"# {'Pre-release' if tag_prerelease else 'Release'} {tag}", ""]
    lines.extend(f"- {repo.display_name}: `{repo.url}` on `{repo.branch}`" for repo in repositories)
    lines.extend([f"- Generated at: `{now}`", ""])
    for repo in repositories:
        append_commit_section(lines, repo, latest_version_tag(repo.checkout_dir, git), git)
    release_notes.parent.mkdir(parents=True, exist_ok=True)
    tag_file.parent.mkdir(parents=True, exist_ok=True)
    release_notes.write_text("\n".join(lines) + "\n", encoding="utf-8")
    tag_file.write_text(tag + "\n", encoding="utf-8")


def remote_tag(git, repository, tag):
    ref = "refs/tags/" + tag
    output = git.capture("ls-remote", "--tags", "--", repository.url, ref, ref + "^{}")
    refs = dict(line.split()[::-1] for line in output.splitlines())
    return refs.get(ref + "^{}", refs.get(ref))


def cached_commit(git, repository):
    directory = repository.checkout_dir
    if directory is None or not COMMIT_PATTERN.fullmatch(repository.revision):
        return None
    if not ((directory / ".git").exists() or ((directory / "HEAD").is_file() and (directory / "objects").is_dir())):
        return None
    result = git.call("cat-file", "-t", repository.revision, cwd=directory, check=False)
    return directory if result.returncode == 0 and result.stdout.strip() == "commit" else None


def push_tags(repositories, git, tag):
    git.call("check-ref-format", "refs/tags/" + tag)
    shared_git = git
    with ExitStack() as stack:
        planned = []
        for repository in repositories:
            git = shared_git.for_repository(repository)
            existing = remote_tag(git, repository, tag)
            revision = repository.revision
            # An exact SHA can be compared without downloading any objects.
            if COMMIT_PATTERN.fullmatch(revision):
                revision = revision.lower()
                if existing is not None:
                    if existing != revision:
                        raise ValueError(f"{repository.display_name}: remote tag {tag} points to {existing}, expected {revision}")
                    print(f"{repository.display_name}: {tag} already points to {existing}", flush=True)
                    continue
            directory = cached_commit(git, repository)
            if directory is not None:
                commit = revision.lower()
            else:
                directory = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="catplay-tag-")))
                git.call("init", "--bare", *(["--object-format=sha256"] if len(revision) == 64
                         and COMMIT_PATTERN.fullmatch(revision) else []), directory)
                # No worktree, unrelated branches, tags, submodules or history.
                # Filtering is best-effort: older servers may ignore blob:none.
                git.call("fetch", "--depth=1", "--filter=blob:none", "--no-tags",
                         "--no-recurse-submodules", "--", repository.url, revision, cwd=directory)
                commit = git.capture("rev-parse", "--verify", "FETCH_HEAD^{commit}", cwd=directory)
            if existing is not None:
                if existing != commit:
                    raise ValueError(f"{repository.display_name}: remote tag {tag} points to {existing}, expected {commit}")
                continue
            # Preserve legacy annotated tags without moving any local ref.
            # Use the caller's configured identity; never invent one for CI.
            identity = git.capture("var", "GIT_COMMITTER_IDENT", cwd=directory)
            tag_object = git.call("mktag", cwd=directory, input=(
                f"object {commit}\ntype commit\ntag {tag}\ntagger {identity}\n\nRelease {tag}\n")).stdout.strip()
            planned.append((repository, directory, commit, tag_object, git))
        # Preflight selected repositories before publishing any tag. Git rejects
        # a concurrent conflicting tag creation; never use force.
        for repository, directory, commit, tag_object, git in planned:
            result = git.call("-c", "push.followTags=false", "push", "--no-verify", "--",
                              repository.url, f"{tag_object}:refs/tags/{tag}", cwd=directory, check=False)
            if result.returncode and remote_tag(git, repository, tag) != commit:
                raise RuntimeError(git.redact(f"{repository.display_name}: tag push failed: {result.stderr.strip()}"))
            print(f"{repository.display_name}: published {tag} -> {commit}", flush=True)


def main(argv=None):
    args = parse_args(argv)
    repositories = [Repository(getattr(args, name + "_display_name"), getattr(args, name + "_repo"),
                               getattr(args, name + "_branch"), getattr(args, name + "_checkout"),
                               getattr(args, name + "_revision", None),
                               not getattr(args, name + "_skip_submodules"),
                               getattr(args, name + "_use_existing_checkout")) for name in ("catplay", "firmware")]
    if args.command == "push-tags":
        repositories = [repo for name, repo in zip(("catplay", "firmware"), repositories)
                        if not getattr(args, "skip_" + name + "_tag")]
    token = args.token_file.read_text(encoding="utf-8").strip() if args.token_file else args.token
    repository_tokens = {getattr(args, name + "_repo"): path.read_text(encoding="utf-8").strip()
                         for name in ("catplay", "firmware")
                         if (path := getattr(args, name + "_token_file")) is not None
                         and (args.command != "push-tags" or not getattr(args, "skip_" + name + "_tag"))}
    git = Git(repositories, token,
              getattr(args, "tagger_name", None), getattr(args, "tagger_email", None), repository_tokens)
    if args.command == "checkout":
        prepare_checkout(repositories, git, release_notes=args.release_notes, tag_file=args.tag_file,
                         tag_prerelease=args.tag_prerelease, tag=args.tag)
    elif args.command == "push-tags":
        push_tags(repositories, git, args.tag)
    else:
        changed = [repo.display_name for repo in repositories
                   if commit_lines(repo, latest_version_tag(repo.checkout_dir, git), git)]
        if not changed:
            print("No changes detected since the latest release tags.")
            return 1
        print("Changes detected in: " + ", ".join(changed))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"nightly.py: {error}", file=sys.stderr)
        sys.exit(1)
