"use server";

import { ApiError, NotAuthenticatedError, submitIdentification } from "@/lib/api";
import type { Identification } from "@/lib/types";

/**
 * The outcome of an identification attempt, as the browser sees it.
 *
 * Errors are returned rather than thrown so the screen can explain what went
 * wrong — "no face was detected", "too many requests" — instead of replacing
 * itself with a generic failure page.
 */
export type IdentifyResult =
  | { ok: true; identification: Identification }
  | { ok: false; code: string; message: string; signedOut?: boolean };

export async function identifyAction(formData: FormData): Promise<IdentifyResult> {
  const image = formData.get("image");
  if (!(image instanceof File) || image.size === 0) {
    return { ok: false, code: "no_image", message: "Choose an image to identify." };
  }

  try {
    return { ok: true, identification: await submitIdentification(image) };
  } catch (error) {
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
    return {
      ok: false,
      code: "unreachable",
      message: "The Hawkeye API could not be reached.",
    };
  }
}
