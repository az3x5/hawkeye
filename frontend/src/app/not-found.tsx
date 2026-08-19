import Link from "next/link";

export default function NotFound() {
  return (
    <div className="card empty">
      <strong>Not found</strong>
      No identification with that id exists.
      <p style={{ marginTop: "1rem" }}>
        <Link href="/">← Review queue</Link>
      </p>
    </div>
  );
}
