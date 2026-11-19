---
name: merge
description: >-
  Merge changes into mcp_gateway's main branch and publish them to origin.
  Use only when the user has explicitly requested merging or landing the change,
  including through Merge Change or /merge; not for review, preparation, passing
  checks, or skill installation alone.
disable-model-invocation: true
metadata:
  delta-action: land
---

# Merge mcp_gateway changes

## Request and destination

An explicit merge request, including `/merge`, authorizes the commits, local
merge, and normal push needed to land the requested change. Do not ask again
whether to merge. Stop only for a genuine blocker, an unsafe operation, or
unresolved scope.

The destination is the `main` branch on the `origin` remote. The repository
currently defines no pull-request, merge-queue, review, or CI process, and no
GitHub CLI is required for this workflow. Do not assume those facts remain true
if repository policy or configuration changes; never use a direct push to
bypass requirements that apply at execution time.

`AGENTS.md` section 8 requires explicit permission for commits, merges, pushes,
and pull requests. The merge request supplies that permission for this workflow
only. It does not authorize force-pushes, history rewrites, branch or worktree
deletion, or inclusion of unrelated work.

## 1. Inspect and scope the change

Work at the repository root and read the current `AGENTS.md`, plus any nested
instruction files applicable to the changed paths. Establish the current branch,
remote, and change state:

```bash
git rev-parse --show-toplevel
git symbolic-ref --quiet --short HEAD
git remote get-url origin
git status --short --branch
git diff
git diff --cached
```

Source: `AGENTS.md` section 8 requires the final `git diff` and working-tree
status review; the other commands are read-only Git inspection.

Review staged, unstaged, and untracked files. Include only the requested change.
Read untracked files before staging them; `git diff` does not show their
contents. Do not commit secrets or local credentials (`AGENTS.md` section 4).
If requested and unrelated edits cannot be separated non-interactively, or the
intended source or destination is ambiguous, stop and ask a focused question.

Run whitespace validation for the pending change:

```bash
git diff --check
git diff --cached --check
```

Do not use `git add -A`, an interactive staging or rebase session, or an editor.
When preparation requires a commit, stage explicit paths and commit with a
concise message describing the requested change:

```bash
git add -- <path>...
git diff --cached
git diff --cached --check
git commit -m "<concise-change-message>"
```

No commit-message convention is currently defined (`AGENTS.md` section 8).
A failed configured signing or hook requirement is a blocker; do not bypass it.

## 2. Fetch and establish `main`

Fetch the current remote state without changing the working tree:

```bash
git fetch origin
git ls-remote --heads origin main
```

Use exactly one of the following cases.

### Initial publication

If local `HEAD` is unborn, proceed only when the current branch is `main` and
`origin/main` does not exist. The requested, reviewed commit prepared above is
the initial commit on `main`; there is no source branch to merge. Continue to
verification, then publish with `git push -u origin main`.

If `origin/main` already exists while local `HEAD` is unborn, stop rather than
creating unrelated histories.

### Existing destination

If `HEAD` has a commit, record the requested source branch before switching.
If the requested change was committed directly on `main`, that branch is both
source and destination and no additional merge is needed.

For a separate source branch, switch to an existing local `main`, or create it
from the fetched remote branch when no local `main` exists:

```bash
git switch main
# Only when local main is absent and origin/main exists:
git switch -c main --track origin/main
```

If unrelated dirty files would be overwritten or make the switch unsafe, stop;
do not stash, delete, or overwrite them automatically.

Compare local `main` with `origin/main`. Any local-only commits must belong to
the requested change. Fast-forward a strictly behind local branch, but stop on
divergence or unrelated destination commits:

```bash
git log --oneline origin/main..main
git merge --ff-only origin/main
```

Record the destination commit immediately before integrating a separate source
branch, then use Git's normal fast-forward-or-merge behavior without opening an
editor:

```bash
git rev-parse HEAD
git merge --no-edit <source-branch>
```

If the source is already contained in `main`, do not manufacture a merge commit.

On conflicts, **pause**. Do not resolve conflicted files, run `git merge
--abort`, or choose a side automatically. Show the conflicted paths, leave the
merge state intact for the user's decision, and report that the change has not
landed:

```bash
git diff --name-only --diff-filter=U
```

## 3. Verify the exact result

At skill installation, `AGENTS.md` sections 4 and 7 contain only TODO
placeholders, and the repository has no source manifests, test tasks, build
scripts, or CI workflows. Therefore there is currently no project test or build
command to run. Do not execute TODO placeholders or invent commands from
another project.

At each merge, use the repository's then-current instructions and actual task,
script, test, and CI definitions. Run every applicable required check against
the exact commit or merged result being landed, not an earlier version. Do not
treat pending, failing, missing, or unverifiable required checks as success. If
new review or remote-check requirements cannot be satisfied and verified under
this direct-push workflow, stop and report the blocker instead of bypassing
them.

For a merge into an existing destination, also check the committed delta from
the pre-merge destination recorded above:

```bash
git diff --check <pre-merge-main-commit>..HEAD
```

For initial publication, the staged `git diff --cached --check` performed
before the initial commit is the applicable whitespace check. In all cases,
review the final diff and status as required by `AGENTS.md` sections 7 and 8.

If verification fails, do not push. Preserve the commits and working state for
investigation, and report that the change has not landed on `origin/main`.

## 4. Publish and confirm

For initial publication use:

```bash
git push -u origin main
```

Otherwise use:

```bash
git push origin main
```

Never force-push. A rejection, authentication failure, permission failure, or
new remote divergence is a blocker; report it rather than rewriting history or
retrying with a destructive option.

After a successful push, fetch and compare the destination commit:

```bash
git fetch origin
git rev-parse HEAD
git rev-parse origin/main
git status --short --branch
```

The two commit IDs must be identical, and `HEAD` must be the intended merged
result. If they differ, do not report success. Leave source branches, unrelated
files, and externally managed worktrees in place; no automatic cleanup is part
of this project's workflow.
