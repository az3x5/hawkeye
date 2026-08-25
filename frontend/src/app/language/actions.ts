"use server";

import {
  ApiError,
  NotAuthenticatedError,
  normalizeLanguageText,
  transliterateLanguageText,
} from "@/lib/api";
import type {
  LanguageNormalization,
  LanguageTransliteration,
  TransliterationDirection,
} from "@/lib/types";

type Failure = { ok: false; code: string; message: string; signedOut?: boolean };

export type NormalizeActionResult =
  | { ok: true; normalization: LanguageNormalization }
  | Failure;

export type TransliterateActionResult =
  | { ok: true; transliteration: LanguageTransliteration }
  | Failure;

export async function normalizeAction(text: string): Promise<NormalizeActionResult> {
  const invalid = validateText(text);
  if (invalid !== null) return invalid;
  try {
    return { ok: true, normalization: await normalizeLanguageText(text) };
  } catch (error) {
    return failure(error);
  }
}

export async function transliterateAction(
  text: string,
  direction: TransliterationDirection,
): Promise<TransliterateActionResult> {
  const invalid = validateText(text);
  if (invalid !== null) return invalid;
  try {
    return {
      ok: true,
      transliteration: await transliterateLanguageText(text, direction),
    };
  } catch (error) {
    return failure(error);
  }
}

function validateText(text: string): Failure | null {
  if (text.trim() === "") {
    return { ok: false, code: "empty_text", message: "Enter some text first." };
  }
  if (text.length > 20_000) {
    return {
      ok: false,
      code: "text_too_long",
      message: "Text must contain 20,000 characters or fewer.",
    };
  }
  return null;
}

function failure(error: unknown): Failure {
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
