import { getTranslations } from "next-intl/server";
import { LoginForm } from "@/components/LoginForm";

export default async function LoginPage() {
  const t = await getTranslations("login");
  return (
    <main className="mx-auto mt-24 max-w-sm px-4">
      <h1 className="mb-6 text-xl font-semibold">{t("title")}</h1>
      <LoginForm />
    </main>
  );
}
