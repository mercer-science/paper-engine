#!/usr/bin/env bash
# Keeps this project in step with its GitHub repository, so the work done on
# one computer is waiting on the other. The hooks in
# .claude/settings.local.json run it; it never needs running by hand.
#
#   sync.sh start   a session opened: commit whatever the last session left
#                   unsaved, bring in GitHub's copy, then send this one up
#   sync.sh end     a session closed, or was /clear-ed: commit and send
#   sync.sh report  say what the last save kept off GitHub, and nothing else
#                   (the session /clear opens, while `end` may still be
#                   pushing)
#
# Closing the terminal window can kill Claude Code before `end` finishes,
# which is why `start` saves first: nothing typed is lost, it only goes up a
# session late.
#
# Large data never reaches GitHub. Before every commit, a new file of KEEP_MB
# or more, or a new folder whose new files add up to DIR_MB or more, is added
# to .gitignore and stays on the computer it was made on - cryo-EM movies,
# tilt series and particle stacks, typically. Nobody can be asked at that
# moment (a session that is closing has no one to answer), so the next
# session opens by saying what was kept off, and the user decides then.
#
# Silent when there is nothing to say. What `start` and `report` print is read
# by Claude at the top of the session, so a problem is reported rather than
# lost. Every path exits 0: a sync that cannot run must never stop a session
# opening.

KEEP_MB=50
DIR_MB=500
KEPT_NOTE=.git/paper-engine-kept-off
KEPT_HEADER="# Kept off GitHub by the project sync: too large for a repository."

cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0

# Only a project that is its own repository. A project folder sitting inside
# some other repository must not commit into that one.
[ -e .git ] || exit 0

say() { echo "Project sync: $*"; }

# Every new, not-yet-ignored path that is too large, as "<bytes>\t<path>",
# a folder ending in "/". One `find` and one `awk` whatever the file count:
# a per-file `stat` costs a process each, and Git Bash on Windows spawns
# slowly enough that a folder of movies would outlast the hook's time limit.
too_large() {
  local others
  others=$(mktemp) || return 0
  git -c core.quotePath=false ls-files --others --exclude-standard \
    >"$others" 2>/dev/null
  if [ -s "$others" ]; then
    find . -path ./.git -prune -o -type f -printf '%s\t%P\n' 2>/dev/null |
      awk -F'\t' -v F=$((KEEP_MB * 1048576)) -v D=$((DIR_MB * 1048576)) '
        NR == FNR { new[$0] = 1; next }
        !($2 in new) { next }
        {
          n = split($2, part, "/"); d = ""
          for (i = 1; i < n; i++) { d = d part[i] "/"; tot[d] += $1 }
          if ($1 >= F) big[$2] = $1
        }
        END {
          # The deepest folders over the limit, so a movie folder is kept off
          # without taking the whole data/ tree with it.
          for (d in tot) if (tot[d] >= D) over[d] = 1
          for (d in over) {
            deepest = 1
            for (e in over) if (e != d && index(e, d) == 1) { deepest = 0; break }
            if (deepest) pick[d] = tot[d]
          }
          for (p in big) {
            inside = 0
            for (d in pick) if (index(p, d) == 1) { inside = 1; break }
            if (inside) continue
            par = p; sub(/[^\/]*$/, "", par)
            count[par]++; loose[p] = par
          }
          # Five large files side by side is a data folder, not five
          # exceptions: one .gitignore line, not five.
          for (p in loose) {
            par = loose[p]
            if (par != "" && count[par] >= 5) pick[par] = tot[par]
            else print big[p] "\t" p
          }
          for (d in pick) print pick[d] "\t" d
        }' "$others" -
  fi
  rm -f "$others"
}

keep_large_off() {
  local list size path
  list=$(too_large)
  [ -n "$list" ] || return 0
  grep -qxF "$KEPT_HEADER" .gitignore 2>/dev/null || {
    printf '\n%s\n%s\n' "$KEPT_HEADER" \
      "# Each stays on the computer it was made on. Delete a line to put it on GitHub." \
      >>.gitignore
  }
  while IFS=$'\t' read -r size path; do
    [ -n "$path" ] || continue
    printf '/%s\n' "$(printf '%s' "$path" | sed 's/[][*?!#\\]/\\&/g')" >>.gitignore
    printf '%s\t%s\n' "$size" "$path" >>"$KEPT_NOTE"
  done <<<"$list"
}

report_kept_off() {
  [ -s "$KEPT_NOTE" ] || return 0
  say "these were too large for GitHub, so they were added to .gitignore and are on this computer only:"
  awk -F'\t' '{
    s = $1 / 1048576; u = "MB"
    if (s >= 1024) { s /= 1024; u = "GB" }
    printf "  - %s (%.1f %s)\n", $2, s, u
  }' "$KEPT_NOTE"
  echo "  Check with the user that this is what they want. Raw cryo-EM data (movies, tilt series, particle stacks) belongs off GitHub; for anything that should be on GitHub, delete its line from .gitignore."
  rm -f "$KEPT_NOTE"
}

save() {
  keep_large_off
  git add -A >/dev/null 2>&1 || return 1
  git diff --cached --quiet && return 1
  git commit -q -m "$1 $(date '+%Y-%m-%d %H:%M')" >/dev/null 2>&1
}

has_remote() { git remote get-url origin >/dev/null 2>&1; }

case "$1" in
  start)
    if [ -z "$(git config user.email)" ]; then
      say "git does not know your name and email on this computer, so nothing can be saved. Ask Claude to set them up."
      exit 0
    fi
    if ! git rev-parse -q --verify HEAD >/dev/null; then
      save "Project created"
    elif save "Auto-save (left unsaved by the last session)"; then
      say "the last session closed before it could save. Its changes are committed now."
    fi
    report_kept_off
    if ! has_remote; then
      say "this project is not connected to a GitHub repository yet, so it is saved on this computer only."
      exit 0
    fi
    branch=$(git symbolic-ref --short HEAD 2>/dev/null) || exit 0
    if ! git fetch -q origin >/dev/null 2>&1; then
      say "could not reach GitHub (offline?). Working from this computer's copy; it will sync next time."
      exit 0
    fi
    if git rev-parse -q --verify "origin/$branch" >/dev/null; then
      if ! git rebase -q "origin/$branch" >/dev/null 2>&1; then
        git rebase --abort >/dev/null 2>&1
        say "this computer and GitHub both changed the same file since the last sync, so nothing was merged or uploaded. Both versions are safe. Ask Claude to resolve it before starting work."
        exit 0
      fi
    fi
    git push -q -u origin HEAD >/dev/null 2>&1 ||
      say "could not upload to GitHub. Everything is saved on this computer and will go up next time."
    ;;
  end)
    save "Auto-save"
    has_remote && git push -q -u origin HEAD >/dev/null 2>&1
    ;;
  report)
    report_kept_off
    ;;
esac
exit 0
