#!/usr/bin/env bash
# Commit a ratcheted file and push it to main, telling a rejected push apart
# from a concurrent one.
#
# ONE implementation, because two copies of a write credential is where they
# drift: the second is a copy, so an edit to the discrimination in the first
# does not follow it. Both jobs that hold the deploy key call this with the
# file they changed and the message that says so.
#
# usage: ratchet_push.sh <path> <commit message>
#
# The credential is a deploy key, not the built-in token, and the difference
# is not a preference. A ruleset may list a job among its required contexts,
# and a commit pushed from here has no green checks yet, so the built-in token
# is refused; GitHub Actions cannot be added as a bypass actor either. A
# deploy key can, it is scoped to this one repository rather than to an
# account, and revoking it is deleting one key.
#
# A rejected push is usually main moving while this ran, and failing the job
# for that would turn a required context red for an ordinary concurrent push.
# So the two causes are distinguished rather than swallowed: if the remote
# really did move past the commit this run measured, the next push retries and
# there is nothing wrong here; if it did not, the push failed for some other
# reason — a revoked key, a ruleset refusal, a hook — and the job says so.
set -u

path=${1:?usage: ratchet_push.sh <path> <commit message>}
message=${2:?usage: ratchet_push.sh <path> <commit message>}

: "${RATCHET_SSH_KEY:?the deploy key is not set; nothing is pushed}"
: "${REPO:?the repository is not set; there is nowhere to push}"

install -d -m 700 ~/.ssh
printf '%s\n' "$RATCHET_SSH_KEY" > ~/.ssh/ratchet
chmod 600 ~/.ssh/ratchet
# Pinned, not scanned: ssh-keyscan would trust whatever answers, and this is
# the one step in this workflow holding a write credential. The value is
# github.com's published ed25519 host key (GET /meta).
printf '%s\n' 'github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl' \
  > ~/.ssh/known_hosts
chmod 600 ~/.ssh/known_hosts
git config user.name 'github-actions[bot]'
git config user.email '41898282+github-actions[bot]@users.noreply.github.com'
git add -- "$path"
# No co-author trailer: no model authored this commit.
git commit -m "$message"
# HEAD:main, never a force: overwriting someone's commit to record a number is
# never the right trade.
export GIT_SSH_COMMAND='ssh -i ~/.ssh/ratchet -o IdentitiesOnly=yes -o UserKnownHostsFile=~/.ssh/known_hosts'
remote="git@github.com:${REPO}.git"
if git push "$remote" HEAD:main; then
  exit 0
fi
git fetch --quiet "$remote" main
if [ "$(git rev-parse HEAD^)" = "$(git rev-parse FETCH_HEAD)" ]; then
  echo "the ratchet push was rejected while main stood still" >&2
  exit 1
fi
echo 'Main moved while this run measured; the next push retries instead.' \
  >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
