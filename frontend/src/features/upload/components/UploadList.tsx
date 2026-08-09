"use client";

import { XCircle } from "lucide-react";

import { UploadRow } from "./UploadRow";

/** An upload the server accepted. It has a document to poll. */
export type AcceptedUpload = { key: string; documentId: string; filename: string };

/**
 * An upload the server refused. Deliberately a different shape.
 *
 * A 409 or a 404 means nothing was enqueued and no run row exists, so there is
 * no document to poll and no status to show. Modelling it as a row with a fake
 * "failed" status would put it next to documents that really did fail
 * processing, and the two need different fixes -- one is "you already uploaded
 * this", the other is "this PDF is broken".
 */
export type RejectedUpload = { key: string; filename: string; detail: string };

export function UploadList({
  accepted,
  rejected,
}: {
  accepted: AcceptedUpload[];
  rejected: RejectedUpload[];
}) {
  if (accepted.length === 0 && rejected.length === 0) return null;

  return (
    <ul className="mt-6 space-y-2">
      {rejected.map((upload) => (
        <li
          key={upload.key}
          className="rounded-md border border-red-500/30 bg-red-500/5 p-3 text-sm"
        >
          <div className="flex items-center gap-2">
            <span className="truncate font-medium">{upload.filename}</span>
            <span className="ml-auto flex shrink-0 items-center gap-1.5 text-red-600">
              <XCircle className="size-4" />
              Not accepted
            </span>
          </div>
          <p className="mt-2 font-mono text-xs text-red-700 dark:text-red-400">{upload.detail}</p>
        </li>
      ))}

      {accepted.map((upload) => (
        <UploadRow
          key={upload.key}
          documentId={upload.documentId}
          filename={upload.filename}
        />
      ))}
    </ul>
  );
}
