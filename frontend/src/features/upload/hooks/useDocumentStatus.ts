"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchDocumentStatus, type DocumentProgress } from "../api/documents";

/** Slow enough not to hammer the API, fast enough that a 4-second job does not
 *  look like it finished instantly with no visible work. */
const POLL_MS = 1500;

export function isTerminal(status: DocumentProgress["status"]): boolean {
  return status === "ready" || status === "failed";
}

/**
 * Poll one document until it settles.
 *
 * `refetchInterval` returns `false` once the document is terminal, which is what
 * stops the polling -- there is no separate effect to unsubscribe. A `ready`
 * document left on screen otherwise queries forever.
 *
 * Deliberately one query per document rather than one list query for all of
 * them: each row starts and stops on its own schedule, and a batch endpoint
 * would have to keep polling for the slowest member of the batch.
 */
export function useDocumentStatus(documentId: string) {
  return useQuery<DocumentProgress>({
    queryKey: ["document-status", documentId],
    queryFn: () => fetchDocumentStatus(documentId),
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      return status && isTerminal(status) ? false : POLL_MS;
    },
    // A document that is still being written to is never fresh; without this the
    // cache would serve the first response back for the default stale time and
    // the row would sit at "Queued" while the worker finished.
    staleTime: 0,
  });
}
