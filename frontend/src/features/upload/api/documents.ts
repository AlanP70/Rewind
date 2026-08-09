/**
 * The two document endpoints, and the types they answer with.
 *
 * These mirror `backend/app/schemas/documents.py` by hand. There is no codegen
 * yet, so the honest description is: this file is a copy that has to be updated
 * when the schema changes. The counts are the part that matters -- see
 * `UploadRow` for why nothing here is turned into a percentage.
 */

const API = process.env.NEXT_PUBLIC_API_URL;

export type DocumentState = "pending" | "processing" | "ready" | "failed";
export type RunState = "queued" | "running" | "succeeded" | "failed";

export type DocumentAccepted = {
  document_id: string;
  job_id: string;
  reused_document: boolean;
};

export type DocumentProgress = {
  document_id: string;
  status: DocumentState;
  chunks_total: number;
  chunks_embedded: number;
  /** Null until a worker has opened a run -- queued but not yet picked up. */
  attempts: number | null;
  run_status: RunState | null;
  error: string | null;
  /** The latest run has claimed `running` for longer than a worker could be alive. */
  stale: boolean;
};

/**
 * Turn a failed response into the server's own message.
 *
 * The backend writes `detail` to be read by a person -- that is the phase's
 * "readable error" bar -- so replacing it with a generic string here would throw
 * away the only useful part of a 409 or a 404.
 */
async function detailOf(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
    return `HTTP ${response.status}`;
  } catch {
    return `HTTP ${response.status}`;
  }
}

export async function uploadDocument(
  file: File,
  courseId: string,
): Promise<DocumentAccepted> {
  const form = new FormData();
  form.append("course_id", courseId);
  form.append("file", file);

  const response = await fetch(`${API}/documents`, { method: "POST", body: form });
  if (!response.ok) throw new Error(await detailOf(response));
  return response.json();
}

export async function fetchDocumentStatus(documentId: string): Promise<DocumentProgress> {
  const response = await fetch(`${API}/documents/${documentId}/status`);
  // A failed *document* is a 200 with `status: "failed"`. Only an unknown
  // document is an error here, so this branch means the request failed, not the
  // processing.
  if (!response.ok) throw new Error(await detailOf(response));
  return response.json();
}
