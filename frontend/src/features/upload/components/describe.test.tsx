/**
 * Phase 2's carried-over assertions, finally written.
 *
 * Phase 2 closed with the `describe()` rules stated in a docstring and checked
 * by nobody, because the frontend had no test runner. The rule that matters is
 * the one about the bar: **a ratio exists only when two real counts do.** The
 * bar is rendered from `stage.ratio`, so anything that puts a number there
 * during extraction produces a progress bar advancing on invented data -- which
 * is exactly what keeping `Progress` out of `components/ui/` was meant to
 * prevent, and it would arrive from the other direction.
 */

import { describe as group, expect, it } from "vitest";

import type { DocumentProgress } from "../api/documents";
import { describe } from "./UploadRow";

function progress(overrides: Partial<DocumentProgress> = {}): DocumentProgress {
  return {
    document_id: "d",
    status: "processing",
    chunks_total: 0,
    chunks_embedded: 0,
    attempts: 1,
    run_status: "running",
    error: null,
    stale: false,
    ...overrides,
  };
}

group("describe", () => {
  it("shows no ratio while extracting, because there is nothing to count yet", () => {
    const stage = describe(progress({ chunks_total: 0 }));

    expect(stage.label).toBe("Extracting text");
    expect(stage.ratio).toBeUndefined();
  });

  it("shows a ratio of the two counts once chunks exist", () => {
    const stage = describe(progress({ chunks_total: 8, chunks_embedded: 3 }));

    expect(stage.label).toBe("Embedding — 3 of 8");
    expect(stage.ratio).toEqual({ done: 3, total: 8 });
  });

  it("never derives the ratio from anything but the two counts", () => {
    // Every other field moved and the ratio did not. Attempts, run state and
    // staleness are all things a "how far along is it" number could plausibly be
    // built from, and none of them is progress.
    const counts = { chunks_total: 8, chunks_embedded: 3 };
    const quiet = describe(progress({ ...counts, attempts: 1, stale: false }));
    const struggling = describe(
      progress({ ...counts, attempts: 4, stale: true, run_status: "queued" }),
    );

    expect(struggling.ratio).toEqual(quiet.ratio);
  });

  it("reports a failure's reason verbatim", () => {
    const stage = describe(
      progress({ status: "failed", error: "the PDF has no extractable text" }),
    );

    expect(stage.error).toBe("the PDF has no extractable text");
  });

  it("says something when a failure recorded no reason", () => {
    const stage = describe(progress({ status: "failed", error: null }));

    // An empty red box reads as a rendering bug rather than a missing reason.
    expect(stage.error).toBe("No reason was recorded.");
    expect(stage.ratio).toBeUndefined();
  });

  it("counts chunks in the ready label and offers no bar", () => {
    const stage = describe(progress({ status: "ready", chunks_total: 9, chunks_embedded: 9 }));

    expect(stage.label).toBe("Ready — 9 chunks");
    expect(stage.ratio).toBeUndefined();
  });

  it("has a queued state distinct from extracting", () => {
    // Both have zero chunks. Only the status tells them apart, and conflating
    // them would show "Extracting text" for a job no worker has picked up.
    expect(describe(progress({ status: "pending" })).label).toBe("Queued");
  });
});
