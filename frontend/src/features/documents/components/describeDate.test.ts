/**
 * `describeDate` -- the function that decides whether a date is shown as a fact
 * or offered as a question.
 *
 * The property under test throughout is that **a candidate never becomes a
 * date.** Every branch that has candidates also has no stored date, and the only
 * branch that produces a day to render is the one reading `occurred_at`. If that
 * ever stopped holding, the timeline would fill up with interpolated dates that
 * look exactly like real ones -- the failure this whole phase is arranged to
 * prevent.
 */

import { describe as group, expect, it } from "vitest";

import type { DocumentDating } from "../api/dating";
import { describeDate, formatDay } from "./describeDate";

function document(overrides: Partial<DocumentDating> = {}): DocumentDating {
  return {
    document_id: "d",
    filename: "lec03.pdf",
    title: "Lecture 3",
    status: "ready",
    occurred_at: null,
    occurred_at_source: null,
    candidates: [],
    reason: "",
    ...overrides,
  };
}

const SYLLABUS = { source: "parsed_syllabus", occurred_on: "2020-09-28" } as const;
const FILENAME = { source: "filename_date", occurred_on: "2020-10-03" } as const;
const GUESS = { source: "inferred_filename", occurred_on: "2020-10-01" } as const;

group("describeDate", () => {
  it("renders a stored date with where it came from", () => {
    const display = describeDate(
      document({ occurred_at: "2020-09-28T00:00:00Z", occurred_at_source: "parsed_syllabus" }),
    );

    expect(display).toEqual({
      kind: "stored",
      day: "Sep 28, 2020",
      provenance: "from the syllabus",
    });
  });

  it("ignores candidates once a date is stored", () => {
    // The backend does not send both, but the branch order is what guarantees a
    // stored date is never displaced by a suggestion about it.
    const display = describeDate(
      document({
        occurred_at: "2020-09-28T00:00:00Z",
        occurred_at_source: "manual",
        candidates: [GUESS],
      }),
    );

    expect(display.kind).toBe("stored");
  });

  it("is undated with a reason when nothing was found", () => {
    const display = describeDate(
      document({ reason: "no date or lecture number in the filename" }),
    );

    expect(display).toEqual({
      kind: "unknown",
      reason: "no date or lecture number in the filename",
    });
  });

  it("passes the reason through verbatim", () => {
    // The backend writes these to be read. Any paraphrase here becomes a second
    // description of one situation, and the two drift.
    const reason = "the syllabus numbers weeks and this filename numbers lectures";

    expect(describeDate(document({ reason }))).toMatchObject({ reason });
  });

  it("offers a single candidate rather than showing it as the date", () => {
    const display = describeDate(document({ candidates: [GUESS], reason: "interpolated" }));

    expect(display).toEqual({ kind: "offered", reason: "interpolated", offer: GUESS });
    // The state carries no `day`, which is the field `DateCell` renders. A guess
    // has no way to reach the date column.
    expect(display).not.toHaveProperty("day");
  });

  it("treats two candidates as a dispute, not as a first choice and a fallback", () => {
    const display = describeDate(
      document({
        candidates: [SYLLABUS, FILENAME],
        reason: "the syllabus says 2020-09-28 but the filename says 2020-10-03",
      }),
    );

    expect(display.kind).toBe("disputed");
    // Both, in the order the server sent them. Dropping the second, or sorting
    // by source, would answer the question instead of asking it.
    expect(display).toMatchObject({ offers: [SYLLABUS, FILENAME] });
  });

  it("totals over the candidate list", () => {
    // Three would only arrive with a third source, but a function that answers
    // for 0, 1 and 2 and falls off the end at 3 is a display that silently drops
    // a candidate on the day one is added.
    const display = describeDate(document({ candidates: [SYLLABUS, FILENAME, GUESS] }));

    expect(display).toMatchObject({ kind: "disputed", offers: [SYLLABUS, FILENAME, GUESS] });
  });

  it("names a stored date whose source is missing rather than showing a blank", () => {
    const display = describeDate(document({ occurred_at: "2020-09-28T00:00:00Z" }));

    expect(display).toMatchObject({ provenance: "source not recorded" });
  });
});

group("formatDay", () => {
  it("reads the stored day, not the browser's day", () => {
    // Dates are stored as midnight UTC. Formatting through the local zone shows
    // Sep 27 to everyone west of Greenwich -- an off-by-one that looks like bad
    // data and only reproduces in some timezones.
    expect(formatDay("2020-09-28T00:00:00Z")).toBe("Sep 28, 2020");
  });

  it("accepts a bare date, which is what candidates carry", () => {
    expect(formatDay("2020-10-03")).toBe("Oct 3, 2020");
  });
});
