#!/bin/sh
# Container entrypoint: migrate, then run the API and the worker side by side.
#
# One service runs both processes. Render does not offer `type: worker` on its
# free instance type, and a second paid service is not worth it for a project
# with no users. render.yaml has the deploy shape; ROADMAP has what this costs
# and what splitting them back out takes.
#
# This is a script rather than an inline command because Render's Docker Command
# field does not reliably parse a compound `sh -c '... && ...'` string — the whole
# thing arrives as one literal token and the container exits 127. It is also the
# stand-in for a pre-deploy hook: Render's free tier has no separate pre-deploy
# command step, so the migration has to run here, in front of everything else.
#
# Migration ordering is no longer a question now that there is one service. It was
# when there were two: both would have run `upgrade head` concurrently against one
# database, and Alembic holds no lock across an upgrade.
set -e

alembic upgrade head

# Past this point a non-zero exit is handled explicitly rather than aborting the
# script — `kill -0` in the loop below fails as a matter of course.
set +e

arq app.workers.settings.WorkerSettings &
worker_pid=$!

uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}" &
api_pid=$!

# The shell stays PID 1 and does not `exec`, which reverses what this file used to
# do. The old comment was right that a shell at PID 1 swallows SIGTERM — but the
# cause is having no handler, not being a shell. Docker signals PID 1 and nothing
# else, so `exec uvicorn` here would leave arq unsignalled and hard-killed on
# every deploy and every free-tier spin-down, stranding whatever job was in
# flight. Trapping and forwarding is the only way both children are told to stop.
#
# arq's own SIGTERM handling is the point of the exercise: it finishes the job in
# flight before exiting, so a deploy costs a delay rather than a stranded run.
shutdown() {
    kill -TERM "$worker_pid" "$api_pid" 2>/dev/null
    wait "$worker_pid" "$api_pid" 2>/dev/null
    exit 0
}
trap shutdown TERM INT

# Exit as soon as *either* child does, rather than waiting for both.
#
# This is the load-bearing half. If arq dies alone and uvicorn keeps serving,
# /health/ready passes, the dashboard shows a healthy service, and uploads are
# accepted and never processed — the exact failure that came from having no worker
# deployed at all, in a shape nothing can observe. Exiting non-zero makes the
# platform restart the container, so a dead worker is a visible restart loop
# instead of silence. Sharing a fate is the deliberate trade for sharing a service.
#
# Polled rather than `wait -n`, which is a bash builtin and this is dash. The poll
# interval also bounds shutdown latency: a trap does not interrupt a foreground
# `sleep`, it runs once that sleep returns.
while kill -0 "$worker_pid" 2>/dev/null && kill -0 "$api_pid" 2>/dev/null; do
    sleep 2
done

kill -TERM "$worker_pid" "$api_pid" 2>/dev/null
exit 1
