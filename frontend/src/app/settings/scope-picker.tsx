import { SCOPES } from "@/lib/types";

/** What each scope actually permits, so a grant is a deliberate choice. */
const MEANING: Record<string, string> = {
  enrol: "register faces and read sample state",
  identify: "submit identifications",
  review: "read the queue and record review decisions",
  language: "normalize, analyze and transliterate Dhivehi text",
  admin: "erase people, manage accounts and credentials",
};

export function ScopePicker({ name = "scopes" }: { name?: string }) {
  return (
    <fieldset className="space-y-1.5">
      <legend className="mb-1 text-sm text-ink-muted">Scopes</legend>
      {SCOPES.map((scope) => (
        <label key={scope} className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            name={name}
            value={scope}
            className="mt-0.5 size-4 rounded border-line bg-surface-sunken"
          />
          <span>
            <span className="identifier text-ink">{scope}</span>
            <span className="ml-2 text-ink-faint">{MEANING[scope]}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}
