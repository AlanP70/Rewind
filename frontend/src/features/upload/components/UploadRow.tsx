"use client";

import { AlertTriangle, CheckCircle2, Clock, Loader2, XCircle } from "lucide-react";

import { useDocumentStatus } from "../hooks/useDocumentStatus";
import type { DocumentProgress } from "../api/documents";

/**
 * What the server's counts mean, in words.
 *
 * **There is no synthetic progress here, and that is the point.** The API
 * returns counts rather than a percentage because until chunking finishes there
 * are no chunks, so any single number would have to invent progress for the
 * extraction phase. Inventing it in the UI instead is the same lie one layer up:
 * it would be rendered confidently and debugged later as if it meant something.
 *
 * So extraction gets an indeterminate spinner -- honest about not knowing -- and
 * a bar appears only during embedding, where `chunks_embedded / chunks_total` is
 * a real ratio of two real counts.
 */
export type Stage = {
  label: string;
  icon: React.ReactNode;
  /** Only set when a genuine ratio exists. Never derived from elapsed time. */
  ratio?: { done: number; total: number };
  error?: string;
};

// Exported for `describe.test.ts`, which is the whole of Phase 2's carried-over
// assertions. The rule it protects -- no bar without two real counts -- is a
// decision, and a decision nothing checks is a comment.
export function describe(progress: DocumentProgress): Stage {
  if (progress.status === "failed") {
    return {
      label: "Failed",
      icon: <XCircle className="size-4 text-red-600" />,
      // Verbatim. The backend writes this to be read.
      error: progress.error ?? "No reason was recorded.",
    };
  }

  if (progress.status === "ready") {
    return {
      label: `Ready — ${progress.chunks_total} chunks`,
      icon: <CheckCircle2 className="size-4 text-green-600" />,
    };
  }

  if (progress.status === "pending") {
    return { label: "Queued", icon: <Clock className="size-4 opacity-60" /> };
  }

  // `processing`. Which half depends on whether chunks exist yet, which is the
  // only signal that distinguishes them -- there is no separate "extracting"
  // status, deliberately.
  if (progress.chunks_total === 0) {
    return {
      label: "Extracting text",
      icon: <Loader2 className="size-4 animate-spin opacity-60" />,
    };
  }

  return {
    label: `Embedding — ${progress.chunks_embedded} of ${progress.chunks_total}`,
    icon: <Loader2 className="size-4 animate-spin opacity-60" />,
    ratio: { done: progress.chunks_embedded, total: progress.chunks_total },
  };
}

export function UploadRow({ documentId, filename }: { documentId: string; filename: string }) {
  const { data, error } = useDocumentStatus(documentId);

  return (
    <li className="rounded-md border border-black/10 p-3 text-sm dark:border-white/15">
      <div className="flex items-center gap-2">
        <span className="truncate font-medium">{filename}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1.5 opacity-80">
          {data ? (
            <>
              {describe(data).icon}
              {describe(data).label}
            </>
          ) : error ? (
            <>
              <XCircle className="size-4 text-red-600" />
              {error.message}
            </>
          ) : (
            <>
              <Loader2 className="size-4 animate-spin opacity-60" />
              Checking&hellip;
            </>
          )}
        </span>
      </div>

      {data && <StageDetail progress={data} />}
    </li>
  );
}

function StageDetail({ progress }: { progress: DocumentProgress }) {
  const stage = describe(progress);

  return (
    <>
      {stage.ratio && (
        <div className="mt-2 h-1 w-full overflow-hidden rounded-full bg-black/10 dark:bg-white/15">
          <div
            className="h-full bg-current opacity-70 transition-[width] duration-300"
            style={{ width: `${(stage.ratio.done / stage.ratio.total) * 100}%` }}
          />
        </div>
      )}

      {stage.error && (
        <p className="mt-2 rounded bg-red-500/10 p-2 font-mono text-xs text-red-700 dark:text-red-400">
          {stage.error}
        </p>
      )}

      {/*
        The stale flag has to be visible or it may as well not be computed. A
        stranded job that only ever reads "Extracting text" forever is the exact
        failure this flag exists to name.
      */}
      {progress.stale && (
        <p className="mt-2 flex items-start gap-1.5 rounded bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
          <span>
            No worker has reported on this for a long time (attempt{" "}
            {progress.attempts ?? "?"}). It was probably interrupted. The queue
            re-runs abandoned jobs by itself; if this persists, check the worker.
          </span>
        </p>
      )}
    </>
  );
}
