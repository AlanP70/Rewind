"use client";

import { useQuery } from "@tanstack/react-query";

import { fetchCourses, type CourseSummary } from "../api/courses";

export function useCourses() {
  return useQuery<CourseSummary[]>({
    queryKey: ["courses"],
    queryFn: fetchCourses,
  });
}
