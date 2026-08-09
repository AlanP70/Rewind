"use client";

import { useMutation } from "@tanstack/react-query";

import { uploadDocument, type DocumentAccepted } from "../api/documents";

/**
 * One upload. The caller decides what a success or a rejection becomes.
 *
 * A rejected upload -- 409 for a document that already has chunks, 404 for an
 * unknown course -- is not a failed document. Nothing was enqueued and no run
 * row exists, so it must not become a row that polls; it is a message about the
 * request. `UploadList` keeps the two in separate shapes for that reason.
 */
export function useUploadDocument() {
  return useMutation<DocumentAccepted, Error, { file: File; courseId: string }>({
    mutationFn: ({ file, courseId }) => uploadDocument(file, courseId),
  });
}
