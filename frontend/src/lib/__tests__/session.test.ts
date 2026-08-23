import { afterEach, describe, expect, it } from "vitest";
import { cookieOptions, cookieSecure } from "../session";

const original = process.env.HAWKEYE_COOKIE_SECURE;

afterEach(() => {
  if (original === undefined) delete process.env.HAWKEYE_COOKIE_SECURE;
  else process.env.HAWKEYE_COOKIE_SECURE = original;
});

describe("cookieSecure", () => {
  it("defaults to secure when nothing is configured", () => {
    delete process.env.HAWKEYE_COOKIE_SECURE;
    expect(cookieSecure()).toBe(true);
  });

  it("can be turned off explicitly, and only by the exact value", () => {
    process.env.HAWKEYE_COOKIE_SECURE = "false";
    expect(cookieSecure()).toBe(false);
    for (const value of ["true", "0", "no", ""]) {
      process.env.HAWKEYE_COOKIE_SECURE = value;
      expect(cookieSecure()).toBe(true);
    }
  });

  it("is decided by its own setting, not the build mode", () => {
    // Tying this to NODE_ENV made a production build served over plain HTTP
    // discard every session, because a browser drops a Secure cookie there.
    // The setting must be able to say "not secure" regardless of NODE_ENV.
    process.env.HAWKEYE_COOKIE_SECURE = "false";
    expect(process.env.NODE_ENV).toBe("test");
    expect(cookieSecure()).toBe(false);
  });
});

describe("cookieOptions", () => {
  it("keeps the cookie away from scripts and other sites", () => {
    const options = cookieOptions(true);
    expect(options.httpOnly).toBe(true);
    expect(options.sameSite).toBe("strict");
    expect(options.path).toBe("/");
  });

  it("takes its default from the configured transport", () => {
    process.env.HAWKEYE_COOKIE_SECURE = "false";
    expect(cookieOptions().secure).toBe(false);
  });
});
