import "@fontsource-variable/inter/index.css";
import "@fontsource-variable/jetbrains-mono/index.css";
import "./globals.css";

import type { Metadata } from "next";

import { Providers } from "@/components/providers";
import { ru } from "@/lib/i18n/ru";

export const metadata: Metadata = {
  title: `${ru.app.title} — ${ru.app.subtitle}`,
  description: ru.app.description,
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="ru" className="h-full" suppressHydrationWarning>
      <body className="min-h-full flex flex-col">
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
