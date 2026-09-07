"use server";

import {
  ApiError,
  chatWithDhivehiBot,
  createLanguageDocument,
  inferDhivehiText,
  NotAuthenticatedError,
  normalizeLanguageText,
  searchLanguageDocuments,
  transliterateLanguageText,
} from "@/lib/api";
import type {
  BotMessage,
  BotResponseLanguage,
  LanguageNormalization,
  LanguageDocument,
  LanguageSearchResponse,
  LanguageTransliteration,
  TransliterationDirection,
  DhivehiInference,
  DhivehiTextTask,
} from "@/lib/types";

type Failure = { ok: false; code: string; message: string; signedOut?: boolean };

export type NormalizeActionResult =
  | { ok: true; normalization: LanguageNormalization }
  | Failure;

export type TransliterateActionResult =
  | { ok: true; transliteration: LanguageTransliteration }
  | Failure;

export type NeuralInferenceActionResult =
  | { ok: true; inference: DhivehiInference }
  | Failure;

export type BotActionResult = { ok: true; inference: DhivehiInference } | Failure;

export type CreateDocumentActionResult =
  | { ok: true; document: LanguageDocument }
  | Failure;

export type SearchDocumentsActionResult =
  | { ok: true; search: LanguageSearchResponse }
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

export async function neuralInferenceAction(
  text: string,
  task: DhivehiTextTask,
): Promise<NeuralInferenceActionResult> {
  const invalid = validateText(text);
  if (invalid !== null) return invalid;
  try {
    return { ok: true, inference: await inferDhivehiText(text, task) };
  } catch (error) {
    return failure(error);
  }
}

export async function botAction(
  messages: BotMessage[],
  responseLanguage: BotResponseLanguage,
): Promise<BotActionResult> {
  if (messages.length === 0 || messages.length > 20) {
    return { ok: false, code: "invalid_chat", message: "Chat must contain 1 to 20 messages." };
  }
  if (messages.at(-1)?.role !== "user") {
    return { ok: false, code: "invalid_chat", message: "Enter a message first." };
  }
  if (
    messages.some((message) => message.content.trim() === "" || message.content.length > 4_000) ||
    messages.reduce((total, message) => total + message.content.length, 0) > 20_000
  ) {
    return { ok: false, code: "invalid_chat", message: "The conversation is too long." };
  }
  try {
    return {
      ok: true,
      inference: await chatWithDhivehiBot(messages, responseLanguage),
    };
  } catch (error) {
    return failure(error);
  }
}

export async function createDocumentAction(body: {
  title: string;
  source: string;
  text: string;
}): Promise<CreateDocumentActionResult> {
  if (body.title.trim() === "" || body.source.trim() === "") {
    return { ok: false, code: "missing_metadata", message: "Title and source are required." };
  }
  const invalid = validateDocumentText(body.text);
  if (invalid !== null) return invalid;
  try {
    return { ok: true, document: await createLanguageDocument(body) };
  } catch (error) {
    return failure(error);
  }
}

export async function searchDocumentsAction(body: {
  text: string;
  source?: string;
}): Promise<SearchDocumentsActionResult> {
  const invalid = validateText(body.text);
  if (invalid !== null) return invalid;
  try {
    return {
      ok: true,
      search: await searchLanguageDocuments({
        text: body.text,
        limit: 10,
        source: body.source?.trim() || undefined,
      }),
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

function validateDocumentText(text: string): Failure | null {
  if (text.trim() === "") {
    return { ok: false, code: "empty_text", message: "Enter document text first." };
  }
  if (text.length > 100_000) {
    return {
      ok: false,
      code: "text_too_long",
      message: "A document must contain 100,000 characters or fewer.",
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
