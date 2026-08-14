/**
 * Mirrors `backend/app/schemas/documents.py` -- `CourseDating`, `DocumentDating`,
 * `DateCandidate`, and the PATCH pair.
 *
 * Hand-mirrored on purpose, like `features/upload/api/documents.ts`: the schemas
 * are a published contract, and a generated client would hide the moment one
 * side changed shape behind a build step nobody reads.
 */

const API = process.env.NEXT_PUBLIC_API_URL;

/**
 * Where a date came from. Four values, and the distinction between the middle
 * two is the whole phase: `filename_date` is a date the filename **states**,
 * `inferred_filename` is one interpolated from a lecture number and never stored
 * by the backend.
 */
export type DateSource =
  | "manual"
  | "parsed_syllabus"
  | "filename_date"
  | "inferred_filename";

/** A date that was worked out and is **not** in the database. */
export type DateCandidate = {
  source: DateSource;
  occurred_on: string;
};

export type DocumentDating = {
  document_id: string;
  filename: string;
  title: string;
  status: string;

  /** The only field that means *stored*. Null is a real, renderable state. */
  occurred_at: string | null;
  occurred_at_source: DateSource | null;

  candidates: DateCandidate[];
  /** Written by the backend to be read by a person. Shown verbatim. */
  reason: string;
};

export type CourseDating = {
  starts_on: string;
  ends_on: string;
  undated: number;
  documents: DocumentDating[];
};

export type DocumentDate = {
  document_id: string;
  occurred_at: string | null;
  occurred_at_source: DateSource | null;
  starts_on: string;
  ends_on: string;
  outside_term: boolean;
};

/** The server's `detail`, or the status code when there isn't one. */
async function detailOf(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body?.detail === "string") return body.detail;
  } catch {
    // Not JSON. Fall through to the status code.
  }
  return `HTTP ${response.status}`;
}

export async function fetchCourseDating(courseId: string): Promise<CourseDating> {
  const response = await fetch(`${API}/documents?course_id=${courseId}`);
  if (!response.ok) throw new Error(await detailOf(response));
  return response.json();
}

/**
 * Set a document's date by hand.
 *
 * **Every write from this UI comes through here, including accepting a
 * candidate**, so every one of them records `manual`. That is not a shortcut:
 * `occurred_at_source` answers who is responsible for the date, and once a
 * person has clicked, the answer is the person -- not the heuristic that put the
 * option in front of them. The route takes no `source` field for exactly this
 * reason.
 */
export async function setDocumentDate(
  documentId: string,
  occurredOn: string,
): Promise<DocumentDate> {
  const response = await fetch(`${API}/documents/${documentId}/date`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ occurred_on: occurredOn }),
  });
  if (!response.ok) throw new Error(await detailOf(response));
  return response.json();
}
