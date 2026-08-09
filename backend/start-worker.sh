#!/bin/sh
# Container entrypoint for the worker service. Same image as the API — the two
# services differ only in which of these two scripts they exec.
set -e

# No `alembic upgrade head` here, unlike start.sh, and that asymmetry is the
# point. Both services redeploy together, so migrating in both means two
# concurrent `upgrade head` runs against one database. Alembic holds no lock
# across an upgrade: the second connection reads `alembic_version`, blocks on the
# first's row lock, and then applies its own steps on top of a history that moved
# underneath it. Migration is the API's job because the API is the one that must
# not serve a request against an old schema. A worker that starts first against a
# stale schema fails loudly on its first query and the platform restarts it —
# an ordering problem that resolves itself, rather than a corrupted history that
# does not.

# exec so arq is PID 1 and receives SIGTERM directly. arq handles it by finishing
# the job in flight before exiting. Behind a shell the signal is swallowed, the
# platform hard-kills after its grace period, and the abandoned job stays claimed
# until arq's in-progress lock expires — the exact window `stale_run_after_seconds`
# is sized around.
exec arq app.workers.settings.WorkerSettings
