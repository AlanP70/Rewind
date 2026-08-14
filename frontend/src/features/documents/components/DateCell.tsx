"use client";

import { CalendarOff } from "lucide-react";

import type { DateDisplay } from "./describeDate";

/**
 * The date column, and the only place a date renders as a date.
 *
 * If nothing is stored, this says so in words and stops. It does not show a
 * candidate greyed out, or in italics, or with a question mark -- every one of
 * those puts a guess where a fact goes and leaves the reader to notice the
 * styling. The offer lives below the row, in `CandidateOffer`.
 */
export function DateCell({ display }: { display: DateDisplay }) {
  if (display.kind === "stored") {
    return (
      <span className="flex shrink-0 flex-col items-end text-right">
        <span className="tabular-nums">{display.day}</span>
        <span className="text-xs opacity-60">{display.provenance}</span>
      </span>
    );
  }

  return (
    <span className="flex shrink-0 items-center gap-1.5 text-sm opacity-60">
      <CalendarOff className="size-4" />
      No date
    </span>
  );
}
