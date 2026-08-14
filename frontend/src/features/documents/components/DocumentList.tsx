"use client";

import { useState } from "react";

import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { courseLabel, useCourses } from "@/lib/courses";

import { useCourseDating, useSetDocumentDate } from "../hooks/useCourseDating";
import { DocumentRow } from "./DocumentRow";

/**
 * Every document in a course, with its date or the reason it hasn't got one.
 *
 * **Undated documents are shown, not hidden and not sorted to the bottom.** They
 * stay in upload order alongside the dated ones, because the count above the
 * list is the honest headline and a list that quietly buries its gaps is how
 * "surfaced, never silently defaulted" turns back into a silent default.
 *
 * There is no "date this course" button here, and no syllabus upload. Both
 * dating runs stay on the CLI: they write across a whole course at once, and a
 * one-click batch write is not something to hand a UI in the slice that first
 * renders the results.
 */
export function DocumentList() {
  const courses = useCourses();
  const [courseId, setCourseId] = useState("");

  const selected = courseId || courses.data?.[0]?.id || null;
  const dating = useCourseDating(selected);
  const setDate = useSetDocumentDate(selected);

  return (
    <Card className="w-full max-w-2xl">
      <CardHeader>
        <CardTitle>Document dates</CardTitle>
      </CardHeader>

      <CardContent>
        {courses.error && (
          <p className="text-sm text-red-600">
            Could not load courses: {courses.error.message}
          </p>
        )}

        {courses.data && courses.data.length > 0 && (
          <label className="block text-sm">
            <span className="opacity-70">Course</span>
            <select
              value={selected ?? ""}
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
        )}

        {courses.data?.length === 0 && (
          <p className="text-sm opacity-70">No courses yet, so there is nothing to date.</p>
        )}

        {dating.isPending && selected && (
          <p className="mt-4 text-sm opacity-60">Loading documents&hellip;</p>
        )}

        {dating.error && (
          <p className="mt-4 text-sm text-red-600">{dating.error.message}</p>
        )}

        {dating.data && dating.data.documents.length === 0 && (
          <p className="mt-4 text-sm opacity-70">
            Nothing uploaded to this course yet.
          </p>
        )}

        {dating.data && dating.data.documents.length > 0 && (
          <>
            {/*
              The count comes from the server rather than being counted here, so
              it cannot disagree with the rows -- and it is stated even when it
              is zero, because "12 of 12 documents have a date" is the sentence
              that tells someone the work is finished.
            */}
            <p className="mt-4 text-sm opacity-70">
              {dating.data.undated} of {dating.data.documents.length} documents have
              no date.
            </p>

            {setDate.error && (
              <p className="mt-2 text-sm text-red-600">{setDate.error.message}</p>
            )}

            <ul className="mt-3 flex flex-col gap-2">
              {dating.data.documents.map((document) => (
                <DocumentRow
                  key={document.document_id}
                  document={document}
                  startsOn={dating.data.starts_on}
                  endsOn={dating.data.ends_on}
                  pending={setDate.isPending}
                  onSetDate={(documentId, occurredOn) =>
                    setDate.mutate({ documentId, occurredOn })
                  }
                />
              ))}
            </ul>
          </>
        )}
      </CardContent>
    </Card>
  );
}
