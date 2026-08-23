/**
 * Runtime configuration.
 *
 * The API address is supplied by the environment. Nothing in the application
 * hard-codes a host: a value baked into a component cannot follow the app from
 * a laptop to a deployment, and every place it is duplicated is a place it can
 * disagree with itself.
 *
 * Read on the server only. The browser never talks to the API directly, so the
 * address is not exposed to it.
 */

export class ConfigurationError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigurationError";
  }
}

/** The Hawkeye API's base URL. */
export function apiBaseUrl(): string {
  const configured = process.env.FACEID_API_URL;
  if (configured === undefined || configured.trim() === "") {
    throw new ConfigurationError(
      "FACEID_API_URL is not set. Point it at the Hawkeye API, e.g. " +
        "http://api:8000 in compose or http://127.0.0.1:8000 for a local backend.",
    );
  }
  return configured.replace(/\/+$/, "");
}

/** Whether the API address has been configured at all. */
export function isConfigured(): boolean {
  try {
    apiBaseUrl();
    return true;
  } catch {
    return false;
  }
}
