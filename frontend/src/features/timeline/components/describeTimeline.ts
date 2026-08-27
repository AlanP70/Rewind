/**
 * What the timeline's claim means, in words. Pure -- no React, no fetch.
 *
 * **Presentation only.** Nothing here decides which document is earliest, groups
 * hits, or filters by relevance; all of that arrives decided from
 * `services/timeline.py`, which is the function slice 4's eval scores. These two
 * functions turn that decision into sentences, and that is the whole job.
 *
 * The sentences are load-bearing. Slice 4 settled that the badge claims
 * **"earliest match"** -- the oldest document *this search retrieved* -- and not
 * "the first time you learned this". The two read almost identically unless the
 * interface says otherwise, so the caveat is written here rather than left to the
 * ROADMAP: a weak claim rendered in the words of a strong one is the failure this
 * decision exists to prevent.
 *
 * The count of documents considered ships in the same breath as the badge for the
 * same reason. "Earliest of 2" and "earliest of 19" are different claims, and a
 * badge that names neither invites the reader to supply the larger number.
 */

import type { SearchBadge, TimelineResults } from "../api/search";

export type TimelineSummary =
  | { kind: "earliest-match"; headline: string; scope: string; caveat: string }
  | { kind: "undetermined"; headline: string; scope: string; reason: string }
  | { kind: "no-matches"; headline: string };

function documents(count: number): string {
  return count === 1 ? "1 document" : `${count} documents`;
}

/**
 * The line above the list: what was found, over how much, and what it does not
 * mean.
 *
 * `undetermined` carries a `reason` rather than a `caveat` on purpose. They are
 * different sentences doing different work -- one qualifies a badge that is
 * shown, the other explains a badge that is absent -- and separate field names
 * stop a component rendering either in the other's place.
 */
export function summarise(results: TimelineResults): TimelineSummary {
  const considered = documents(results.documents_considered);

  switch (results.badge.claim) {
    case "earliest-match": {
      const tied = results.badge.document_ids.length;
      return {
        kind: "earliest-match",
        headline:
          tied === 1
            ? "Earliest match"
            : `${tied} documents tie for earliest match`,
        scope: `of ${considered} that matched`,
        caveat:
          "The oldest of what this search found — not necessarily the first time this appeared in your material.",
      };
    }
    case "undetermined":
      return {
        kind: "undetermined",
        headline: "Earliest match undetermined",
        scope: `across ${considered} that matched`,
        reason: `${documents(results.badge.undated_count)} here ${
          results.badge.undated_count === 1 ? "has" : "have"
        } no date, and an undated document could come before any of these.`,
      };
    case "no-matches":
      return { kind: "no-matches", headline: "Nothing matched this search." };
  }
}

/**
 * The label on one row, or `null` for no badge.
 *
 * A tie names its size rather than picking a winner: two lectures on the same day
 * are both earliest, and choosing between them would invent a precision these
 * dates do not have.
 */
export function badgeFor(badge: SearchBadge, documentId: string): string | null {
  if (badge.claim !== "earliest-match") return null;
  if (!badge.document_ids.includes(documentId)) return null;
  return badge.document_ids.length === 1
    ? "Earliest match"
    : `Earliest match (tied, ${badge.document_ids.length})`;
}
