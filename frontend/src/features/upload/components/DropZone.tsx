"use client";

import { UploadCloud } from "lucide-react";
import { useRef, useState } from "react";

/**
 * Drag PDFs here, or click to pick them.
 *
 * Hand-rolled rather than a shadcn primitive because there is no shadcn drop
 * zone -- it is a label wrapping a hidden file input, which is also what makes
 * it keyboard-reachable for free.
 */
export function DropZone({
  onFiles,
  disabled,
}: {
  onFiles: (files: File[]) => void;
  disabled?: boolean;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  function accept(list: FileList | null) {
    if (!list) return;
    // Filtered here rather than left to the server: dropping a folder of mixed
    // files should not fire off uploads that are all going to be rejected.
    const pdfs = Array.from(list).filter((file) => file.type === "application/pdf");
    if (pdfs.length > 0) onFiles(pdfs);
  }

  return (
    <label
      onDragOver={(event) => {
        event.preventDefault();
        if (!disabled) setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(event) => {
        event.preventDefault();
        setOver(false);
        if (!disabled) accept(event.dataTransfer.files);
      }}
      className={[
        "flex cursor-pointer flex-col items-center gap-2 rounded-lg border border-dashed p-8 text-center transition-colors",
        over ? "border-current bg-black/5 dark:bg-white/10" : "border-black/20 dark:border-white/20",
        disabled ? "pointer-events-none opacity-50" : "",
      ].join(" ")}
    >
      <input
        ref={input}
        type="file"
        accept="application/pdf"
        multiple
        className="sr-only"
        disabled={disabled}
        onChange={(event) => {
          accept(event.target.files);
          // Cleared so re-picking the same file fires `change` again.
          event.target.value = "";
        }}
      />
      <UploadCloud className="size-6 opacity-60" />
      <span className="text-sm font-medium">Drop PDFs here, or click to choose</span>
      <span className="text-xs opacity-60">Several at once is fine</span>
    </label>
  );
}
