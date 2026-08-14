/**
 * What one document's dating state means, in words. Pure -- no React, no fetch.
 *
 * **The guess and the fact must not read in the same register.** A stored date
 * is a value: it renders in the date column, in the same place and weight as
 * every other document's date. A candidate is not a value and must never be
 * rendered as one -- it renders as a *verb*, on a button, below the row. Someone
 * scanning the column sees only dates the system actually holds, so an empty
 * cell means "no date" and never "a date we didn't feel sure enough to put here".
 *
 * Four states, not "dated or not", because the undated ones differ in what the
 * person is being asked to do:
 *
 *   stored     a date exists, and `provenance` says where it came from.
 *   unknown    nothing was found. `reason` says what was missing.
 *   offered    one candidate. An offer: accept it or type your own.
 *   disputed   two candidates that disagree. A decision, not an offer.
 *
 * `disputed` is unreachable from the API today -- a syllabus/filename conflict
 * needs the parsed schedule to still exist at read time, and nothing persists it
 * (ROADMAP, Deferred). It is written anyway because this function has to total
 * over the candidate list regardless, and the alternative -- handling one
 * candidate and letting the rest fall through -- silently drops the second one
 * on the day persistence arrives, which is the day the second one matters most.
 */

import type { DateCandidate, DateSource, DocumentDating } from "../api/dating";

export type DateDisplay =
  | { kind: "stored"; day: string; provenance: string }
  | { kind: "unknown"; reason: string }
  | { kind: "offered"; reason: string; offer: DateCandidate }
  | { kind: "disputed"; reason: string; offers: DateCandidate[] };

/**
 * `YYYY-MM-DD` as a readable day, in UTC.
 *
 * The timezone is not a detail here. Dates are stored as midnight UTC, so
 * letting `toLocaleDateString` use the browser's zone shows the previous day to
 * everyone west of Greenwich -- an off-by-one that appears only for some users,
 * only sometimes, and looks like bad data rather than a bug.
 */
export function formatDay(iso: string): string {
  return new Date(`${iso.slice(0, 10)}T00:00:00Z`).toLocaleDateString("en-US", {
    timeZone: "UTC",
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

/** How a stored date got there, in the past tense. Read, not clicked. */
export function provenanceOf(source: DateSource | null): string {
  switch (source) {
    case "manual":
      return "set by hand";
    case "parsed_syllabus":
      return "from the syllabus";
    case "filename_date":
      return "stated in the filename";
    case "inferred_filename":
      return "interpolated from the lecture number";
    default:
      return "source not recorded";
  }
}

/**
 * What a candidate is offering, as something to do.
 *
 * Deliberately a verb phrase and deliberately not a confidence. There is no
 * "recommended" and no ranking: `source` is provenance, and the two sources in a
 * disagreement are two pieces of testimony, not a strong one and a weak one.
 */
export function offerLabel(candidate: DateCandidate): string {
  switch (candidate.source) {
    case "parsed_syllabus":
      return `Use ${formatDay(candidate.occurred_on)}, from the syllabus`;
    case "filename_date":
      return `Use ${formatDay(candidate.occurred_on)}, from the filename`;
    case "inferred_filename":
      return `Use ${formatDay(candidate.occurred_on)}, a guess from the number`;
    default:
      return `Use ${formatDay(candidate.occurred_on)}`;
  }
}

export function describeDate(document: DocumentDating): DateDisplay {
  if (document.occurred_at) {
    return {
      kind: "stored",
      day: formatDay(document.occurred_at),
      provenance: provenanceOf(document.occurred_at_source),
    };
  }

  if (document.candidates.length === 0) {
    return { kind: "unknown", reason: document.reason };
  }

  if (document.candidates.length === 1) {
    return { kind: "offered", reason: document.reason, offer: document.candidates[0] };
  }

  return { kind: "disputed", reason: document.reason, offers: document.candidates };
}
