"use client";

import { useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { courseLabel, useCourses } from "@/lib/courses";
import { useUploadDocument } from "../hooks/useUploadDocument";
import { DropZone } from "./DropZone";
import { UploadList, type AcceptedUpload, type RejectedUpload } from "./UploadList";

/**
 * The whole upload feature. Owns the list of uploads made in this session.
 *
 * `useState` rather than Zustand: this is one page's state, and nothing else
 * reads it. Zustand is in the stack for state genuinely shared across routes,
 * and reaching for it here would be a store with one consumer.
 *
 * The list is intentionally session-scoped -- it is a record of what *this*
 * visit uploaded, not a library view. A page listing every document belongs to a
 * later phase, and pretending this is one would mean an empty screen on reload
 * that looks like data loss.
 */
export function UploadPanel() {
  const courses = useCourses();
  const upload = useUploadDocument();

  const [courseId, setCourseId] = useState("");
  const [accepted, setAccepted] = useState<AcceptedUpload[]>([]);
  const [rejected, setRejected] = useState<RejectedUpload[]>([]);

  // The first course is the current one -- `GET /courses` orders by term
  // descending -- so preselect it rather than making the common case a choice.
  const selected = courseId || courses.data?.[0]?.id || "";

  async function handleFiles(files: File[]) {
    // Fired together, not awaited in sequence: two documents uploading at once
    // is a case the backend is built for, and serialising here would hide it.
    await Promise.all(
      files.map(async (file) => {
        // Unique per upload, so re-uploading the same filename does not collide
        // with the earlier row's React key.
        const key = `${file.name}-${crypto.randomUUID()}`;
        try {
          const result = await upload.mutateAsync({ file, courseId: selected });
          setAccepted((rows) => [
            { key, documentId: result.document_id, filename: file.name },
            ...rows,
          ]);
        } catch (error) {
          setRejected((rows) => [
            { key, filename: file.name, detail: (error as Error).message },
            ...rows,
          ]);
        }
      }),
    );
  }

  return (
    <Card className="w-full max-w-xl">
      <CardHeader>
        <CardTitle>Upload course material</CardTitle>
      </CardHeader>

      <CardContent>
        {courses.isPending && <p className="text-sm opacity-60">Loading courses&hellip;</p>}

        {courses.error && (
          <p className="text-sm text-red-600">Could not load courses: {courses.error.message}</p>
        )}

        {courses.data?.length === 0 && (
          <p className="text-sm opacity-70">
            No courses yet. Create one with{" "}
            <code className="font-mono text-xs">
              uv run python -m app.cli create-course
            </code>{" "}
            — term dates are real data, so they are entered deliberately rather
            than typed into a form.
          </p>
        )}

        {courses.data && courses.data.length > 0 && (
          <>
            <label className="block text-sm">
              <span className="opacity-70">Course</span>
              <select
                value={selected}
                onChange={(event) => setCourseId(event.target.value)}
                className="mt-1 w-full rounded-md border border-black/15 bg-transparent p-2 text-sm dark:border-white/20"
              >
                {courses.data.map((course) => (
                  <option key={course.id} value={course.id}>
                    {courseLabel(course)}
                  </option>
                ))}
              </select>
            </label>

            <div className="mt-4">
              <DropZone onFiles={handleFiles} disabled={!selected} />
            </div>
          </>
        )}

        <UploadList accepted={accepted} rejected={rejected} />
      </CardContent>
    </Card>
  );
}
