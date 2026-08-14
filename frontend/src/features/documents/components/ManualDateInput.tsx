"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";

/**
 * Type a date in. Present on every undated row, next to whatever is offered.
 *
 * `min` and `max` are the course's term, so the field shows the range a date is
 * being judged against. They are a hint, not a rule -- the backend accepts a
 * manual date outside the term deliberately (a make-up class in exam week is
 * real), and reports `outside_term` rather than refusing. Enforcing it here
 * would take away the one override the whole funnel is built around.
 */
export function ManualDateInput({
  startsOn,
  endsOn,
  onSubmit,
  disabled,
}: {
  startsOn: string;
  endsOn: string;
  onSubmit: (occurredOn: string) => void;
  disabled: boolean;
}) {
  const [value, setValue] = useState("");

  return (
    <form
      className="flex items-center gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (value) onSubmit(value);
      }}
    >
      <input
        type="date"
        aria-label="Date this document"
        className="h-7 rounded-md border border-black/15 bg-transparent px-2 text-[0.8rem] dark:border-white/20"
        min={startsOn}
        max={endsOn}
        value={value}
        disabled={disabled}
        onChange={(event) => setValue(event.target.value)}
      />
      <Button type="submit" variant="ghost" size="sm" disabled={disabled || !value}>
        Set date
      </Button>
    </form>
  );
}
