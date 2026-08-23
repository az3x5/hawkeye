"use server";

import { ApiError, NotAuthenticatedError, fetchFaceSample, submitEnrolment } from "@/lib/api";
import type { Enrolment, FaceSample } from "@/lib/types";

export type EnrolResult =
  | { ok: true; enrolment: Enrolment }
  | { ok: false; code: string; message: string; signedOut?: boolean };

export type SampleResult =
  | { ok: true; sample: FaceSample }
  | { ok: false; code: string; message: string };

function failure(error: unknown): { ok: false; code: string; message: string; signedOut?: boolean } {
  if (error instanceof NotAuthenticatedError) {
    return {
      ok: false,
      code: "not_authenticated",
      message: "Your session has ended. Sign in again to continue.",
      signedOut: true,
    };
  }
  if (error instanceof ApiError) {
    return {
      ok: false,
      code: error.code,
      message: error.message,
      signedOut: error.status === 401,
    };
  }
  return { ok: false, code: "unreachable", message: "The Hawkeye API could not be reached." };
}

export async function enrolAction(formData: FormData): Promise<EnrolResult> {
  const image = formData.get("image");
  if (!(image instanceof File) || image.size === 0) {
    return { ok: false, code: "no_image", message: "Choose an image to enrol." };
  }

  const source = String(formData.get("source") ?? "").trim();
  const externalId = String(formData.get("external_id") ?? "").trim();
  const localId = String(formData.get("local_id") ?? "").trim();
  const capturedAt = String(formData.get("captured_at") ?? "").trim();

  if (source === "") {
    return { ok: false, code: "no_source", message: "A source is required." };
  }
  // The API requires one too; saying so here saves a round trip and explains
  // why: without an external identifier a repeat could not be recognised.
  if (externalId === "" && localId === "") {
    return {
      ok: false,
      code: "no_identifier",
      message: "Give an external id or a local id, so a repeated submission can be recognised.",
    };
  }

  try {
    const enrolment = await submitEnrolment({
      source,
      externalId: externalId || null,
      localId: localId || null,
      // The browser sends its own offset alongside the local time, because a
      // datetime-local field carries none and assuming UTC would record the
      // wrong capture time for everyone outside it.
      capturedAt: capturedAt || null,
      image,
    });
    return { ok: true, enrolment };
  } catch (error) {
    return failure(error);
  }
}

/** Re-read one sample, for following it through detection and embedding. */
export async function sampleStateAction(faceSampleUuid: string): Promise<SampleResult> {
  try {
    return { ok: true, sample: await fetchFaceSample(faceSampleUuid) };
  } catch (error) {
    return failure(error);
  }
}
