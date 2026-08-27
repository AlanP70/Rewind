/**
 * Mirrors `backend/app/schemas/search.py` -- `SearchRequest` and the timeline
 * response it answers with.
 *
 * Hand-mirrored, like `features/documents/api/dating.ts`: the schemas are a
 * published contract, and a generated client hides the moment one side changed
 * shape behind a build step nobody reads.
 *
 * **The badge arrives decided.** There is no ordering, grouping or
 * first-occurrence logic on this side of the wire -- the backend's
 * `services/timeline.py` makes the claim and slice 4's eval scores that same
 * function. A copy of the rule here would be a second implementation of the
 * product's headline behaviour, measured in neither place once the two drifted.
 */

const API = process.env.NEXT_PUBLIC_API_URL;

/**
 * Redeclared rather than imported from `features/documents`. Features do not
 * import from each other (CLAUDE.md invariant 6), and both of these mirror the
 * same backend enum, which is the contract they actually share.
 */
export type DateSource =
  | "manual"
  | "parsed_syllabus"
  | "filename_date"
  | "inferred_filename";

/**
 * One passage. Chunk-level fields only -- no date, because a date belongs to the
 * document group and a passage placed independently of its document is how "hits
 * group per document" comes undone one component at a time.
 */
export type TimelineHit = {
  chunk_id: string;
  /** The deep link: the page opens the PDF, the offsets place our highlight. */
  page_number: number;
  char_start: number;
  char_end: number;
  content: string;
  /** Cosine distance, 0 = identical. Raw; there is no calibration behind it. */
  distance: number;
};

export type TimelineEntry = {
  document_id: string;
  course_id: string;
  document_title: string;
  course_name: string;
  occurred_at: string | null;
  occurred_at_source: DateSource | null;
  /** Best first, so the deep link goes to the strongest passage. */
  hits: TimelineHit[];
};

/**
 * Which claim the backend is making about the ordering. The variant name crosses
 * the wire so the interface switches on the claim rather than on a zero count --
 * `undetermined` and `no-matches` both mean "no badge" and need different words.
 *
 * `earliest-match` is deliberately not called `first`. It says these are the
 * oldest documents *this search retrieved*, which is what the system can
 * actually verify. See ROADMAP, "Settled: stop claiming first".
 */
export type SearchBadge =
  | { claim: "earliest-match"; document_ids: string[] }
  | { claim: "undetermined"; undated_count: number }
  | { claim: "no-matches" };

export type TimelineResults = {
  query: string;
  badge: SearchBadge;
  /**
   * How many documents matched, counted before anything was grouped or split.
   * Required, and rendered: "earliest of 2" and "earliest of 19" are different
   * claims, and this is the only field that tells the reader which one they have.
   */
  documents_considered: number;
  dated: TimelineEntry[];
  undated: TimelineEntry[];
  embed_ms: number;
  query_ms: number;
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

/**
 * `course_id` is optional and omitted by default. Searching a whole degree is the
 * product; scoping to one course is the special case.
 */
export async function searchTimeline(
  query: string,
  courseId?: string,
): Promise<TimelineResults> {
  const response = await fetch(`${API}/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(courseId ? { query, course_id: courseId } : { query }),
  });
  if (!response.ok) throw new Error(await detailOf(response));
  return response.json();
}
