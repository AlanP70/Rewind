#!/bin/sh
# Container entrypoint: migrate, then serve.
#
# This is a script rather than an inline command because Render's Docker Command
# field does not reliably parse a compound `sh -c '... && ...'` string — the whole
# thing arrives as one literal token and the container exits 127. It is also the
# stand-in for a pre-deploy hook: Render's free tier has no separate pre-deploy
# command step, so the migration has to run here, in front of the server.
set -e

alembic upgrade head

# exec so uvicorn becomes PID 1 and receives SIGTERM directly — without it the
# shell holds PID 1, swallows the signal, and the platform kills the container
# after its grace period instead of letting connections drain.
exec uvicorn app.main:app --host 0.0.0.0 --port "${PORT:-8000}"
