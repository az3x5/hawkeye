import Link from "next/link";

export default function NotFound() {
  return (
    <>
      <h1>Not found</h1>
      <p className="lede">No identification with that id exists.</p>
      <p>
        <Link href="/">← Review queue</Link>
      </p>
    </>
  );
}
