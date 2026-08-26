import { redirect } from "next/navigation";
import { LanguageWorkspace } from "@/app/language/language-workspace";
import { SemanticSearch } from "@/app/language/semantic-search";
import { PageHeader } from "@/components/shell/page-header";
import { fetchIdentity } from "@/lib/api";

export const dynamic = "force-dynamic";

export default async function LanguagePage() {
  const identity = await fetchIdentity().catch(() => null);
  if (identity === null) redirect("/sign-in");

  return (
    <>
      <PageHeader
        title="Dhivehi NLP"
        description="Normalize mixed Dhivehi and English text, inspect script boundaries, and transliterate explicitly between Romanized Dhivehi and Thaana."
      />
      {identity.scopes.includes("language") ? (
        <>
          <LanguageWorkspace />
          <SemanticSearch />
        </>
      ) : (
        <p className="panel px-6 py-10 text-center text-sm text-ink-muted">
          Your account does not hold the <span className="identifier">language</span> scope.
          An administrator can grant it.
        </p>
      )}
    </>
  );
}
