/**
 * The words the timeline puts on screen. Pure functions, so no DOM and no runner
 * setup beyond vitest -- same arrangement as `describeDate.test.ts`.
 *
 * These pin the *claim*, not the phrasing. The assertions that matter are the
 * ones checking that "first" never appears and that the count of documents
 * considered is present: those are the two things slice 4 decided, and both are
 * the kind of thing a later copy-edit removes without noticing.
 */

import { describe, expect, it } from "vitest";

import type { SearchBadge, TimelineResults } from "../api/search";
import { badgeFor, summarise } from "./describeTimeline";

function results(
  badge: SearchBadge,
  documentsConsidered: number,
): TimelineResults {
  return {
    query: "recursion",
    badge,
    documents_considered: documentsConsidered,
    dated: [],
    undated: [],
    embed_ms: 0,
    query_ms: 0,
  };
}

describe("summarise", () => {
  it("names how many documents the claim was made over", () => {
    const summary = summarise(results({ claim: "earliest-match", document_ids: ["a"] }, 7));

    expect(summary.kind).toBe("earliest-match");
    expect(summary.kind === "earliest-match" && summary.scope).toContain("7 documents");
  });

  it("never calls the earliest match the first occurrence", () => {
    // The whole of slice 4's decision, in one assertion. "Earliest match" and
    // "first occurrence" are different promises, and the second one is not
    // measurable against this corpus -- see ROADMAP, "Settled: stop claiming
    // first". A copy-edit that reintroduces the stronger word fails here.
    const summary = summarise(results({ claim: "earliest-match", document_ids: ["a"] }, 7));
    const words = summary.kind === "earliest-match"
      ? `${summary.headline} ${summary.scope} ${summary.caveat}`
      : "";

    expect(words.toLowerCase()).not.toContain("first time you");
    expect(words.toLowerCase()).toContain("not necessarily the first time");
    expect(summary.kind === "earliest-match" && summary.headline).toBe("Earliest match");
  });

  it("says a tie is a tie instead of naming a winner", () => {
    const summary = summarise(
      results({ claim: "earliest-match", document_ids: ["a", "b"] }, 7),
    );

    expect(summary.kind === "earliest-match" && summary.headline).toBe(
      "2 documents tie for earliest match",
    );
  });

  it("explains a suppressed badge instead of showing nothing", () => {
    // An absent badge with no sentence beside it reads as a bug. The undated
    // documents are still in the results; this is what says why they matter.
    const summary = summarise(results({ claim: "undetermined", undated_count: 1 }, 4));

    expect(summary.kind).toBe("undetermined");
    expect(summary.kind === "undetermined" && summary.reason).toContain(
      "could come before",
    );
    expect(summary.kind === "undetermined" && summary.scope).toContain("4 documents");
  });

  it("keeps an absent badge apart from an empty search", () => {
    // `undetermined` and `no-matches` both mean no badge, and collapsing them
    // would tell someone whose material simply is not dated that nothing matched.
    expect(summarise(results({ claim: "no-matches" }, 0)).kind).toBe("no-matches");
    expect(summarise(results({ claim: "undetermined", undated_count: 2 }, 2)).kind).toBe(
      "undetermined",
    );
  });

  it("counts one document without pluralising it", () => {
    const summary = summarise(results({ claim: "earliest-match", document_ids: ["a"] }, 1));

    expect(summary.kind === "earliest-match" && summary.scope).toContain("1 document ");
  });
});

describe("badgeFor", () => {
  it("badges only the documents the backend named", () => {
    const badge: SearchBadge = { claim: "earliest-match", document_ids: ["a"] };

    expect(badgeFor(badge, "a")).toBe("Earliest match");
    expect(badgeFor(badge, "b")).toBeNull();
  });

  it("badges every document in a tie, and says how many", () => {
    const badge: SearchBadge = { claim: "earliest-match", document_ids: ["a", "b"] };

    expect(badgeFor(badge, "a")).toBe("Earliest match (tied, 2)");
    expect(badgeFor(badge, "b")).toBe("Earliest match (tied, 2)");
  });

  it("badges nothing when the claim is not being made", () => {
    // The mutation worth catching: badging the first dated row regardless. That
    // is what the backend's suppression rule exists to prevent, and a renderer
    // that ignores the claim undoes it on the way to the screen.
    expect(badgeFor({ claim: "undetermined", undated_count: 1 }, "a")).toBeNull();
    expect(badgeFor({ claim: "no-matches" }, "a")).toBeNull();
  });
});
