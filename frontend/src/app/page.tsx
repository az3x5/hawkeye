import { redirect } from "next/navigation";

/** The workstation opens on the dashboard. */
export default function IndexPage() {
  redirect("/dashboard");
}
