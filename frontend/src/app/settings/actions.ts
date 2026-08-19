"use server";

import { revalidatePath } from "next/cache";
import {
  ApiError,
  changeOwnPassword,
  createAccount,
  issueToken,
  revokeToken,
  setAccountDisabled,
  setAccountPassword,
} from "@/lib/api";
import type { IssuedToken } from "@/lib/types";

/**
 * Outcomes of administrative actions.
 *
 * Failures are returned rather than thrown so a form can explain itself — the
 * API's own message says whether a password was too short, an email was taken
 * or the current password was wrong.
 */
export type ActionResult =
  | { ok: true; message: string }
  | { ok: false; code: string; message: string };

export type IssueResult =
  | { ok: true; token: IssuedToken }
  | { ok: false; code: string; message: string };

function failure(error: unknown): { ok: false; code: string; message: string } {
  if (error instanceof ApiError) {
    return { ok: false, code: error.code, message: error.message };
  }
  return { ok: false, code: "unreachable", message: "The Face ID API could not be reached." };
}

export async function changePasswordAction(
  _previous: ActionResult | null,
  formData: FormData,
): Promise<ActionResult> {
  const current = String(formData.get("current_password") ?? "");
  const next = String(formData.get("new_password") ?? "");
  const confirm = String(formData.get("confirm_password") ?? "");

  if (current === "" || next === "") {
    return { ok: false, code: "incomplete", message: "Both passwords are required." };
  }
  // Checked here as well as by the API: a typo in a field nobody can read back
  // would otherwise lock the account out of its own sessions.
  if (next !== confirm) {
    return { ok: false, code: "mismatch", message: "The new passwords do not match." };
  }

  try {
    await changeOwnPassword(current, next);
  } catch (error) {
    return failure(error);
  }
  return {
    ok: true,
    message: "Password changed. Every session was revoked, including this one — sign in again.",
  };
}

export async function createAccountAction(
  _previous: ActionResult | null,
  formData: FormData,
): Promise<ActionResult> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  const scopes = formData.getAll("scopes").map(String);

  if (email === "" || password === "") {
    return { ok: false, code: "incomplete", message: "Email and password are required." };
  }
  if (scopes.length === 0) {
    return { ok: false, code: "no_scopes", message: "Choose at least one scope." };
  }

  try {
    await createAccount({ email, password, scopes });
  } catch (error) {
    return failure(error);
  }
  revalidatePath("/settings");
  return { ok: true, message: `Created ${email}.` };
}

export async function setAccountPasswordAction(
  userUuid: string,
  password: string,
): Promise<ActionResult> {
  try {
    await setAccountPassword(userUuid, password);
  } catch (error) {
    return failure(error);
  }
  revalidatePath("/settings");
  return { ok: true, message: "Password set. That account's sessions were revoked." };
}

export async function setAccountDisabledAction(
  userUuid: string,
  disabled: boolean,
): Promise<ActionResult> {
  try {
    await setAccountDisabled(userUuid, disabled);
  } catch (error) {
    return failure(error);
  }
  revalidatePath("/settings");
  return { ok: true, message: disabled ? "Account disabled." : "Account enabled." };
}

export async function issueTokenAction(
  _previous: IssueResult | null,
  formData: FormData,
): Promise<IssueResult> {
  const subject = String(formData.get("subject") ?? "").trim();
  const kind = String(formData.get("kind") ?? "service");
  const scopes = formData.getAll("scopes").map(String);
  const days = String(formData.get("expires_in_days") ?? "").trim();
  const never = formData.get("never_expires") === "on";

  if (subject === "") {
    return { ok: false, code: "incomplete", message: "A subject is required." };
  }
  if (scopes.length === 0) {
    return { ok: false, code: "no_scopes", message: "Choose at least one scope." };
  }

  try {
    const token = await issueToken({
      subject,
      kind,
      scopes,
      expires_in_days: days === "" ? null : Number(days),
      never_expires: never,
    });
    revalidatePath("/settings");
    return { ok: true, token };
  } catch (error) {
    return failure(error);
  }
}

export async function revokeTokenAction(tokenUuid: string): Promise<ActionResult> {
  try {
    await revokeToken(tokenUuid);
  } catch (error) {
    return failure(error);
  }
  revalidatePath("/settings");
  return { ok: true, message: "Credential revoked." };
}
