import type { Metadata } from "next";
import type { CSSProperties, ReactNode } from "react";
import { headers } from "next/headers";
import "../styles/globals.css";
import "../styles/print.css";

import { defaultTenantTheme, tenantThemeToCssVars } from "../lib/tenant-theme";
import { getPublicTenantTheme } from "../lib/api-public";
import { getTenantContext } from "../lib/tenant-context";
import { buildMetadataBase, SITE_DESCRIPTION, SITE_NAME } from "../lib/seo";

type RootLayoutProps = {
  children: ReactNode;
};

export async function generateMetadata(): Promise<Metadata> {
  const headerList = headers();
  const tenantHost = headerList.get("x-forwarded-host") ?? headerList.get("host") ?? "";

  return {
    metadataBase: buildMetadataBase(tenantHost),
    applicationName: SITE_NAME,
    title: SITE_NAME,
    description: SITE_DESCRIPTION,
    openGraph: {
      type: "website",
      siteName: SITE_NAME,
      locale: "cs_CZ",
      title: SITE_NAME,
      description: SITE_DESCRIPTION,
    },
    twitter: {
      card: "summary_large_image",
      title: SITE_NAME,
      description: SITE_DESCRIPTION,
    },
  };
}

export default async function RootLayout({ children }: RootLayoutProps) {
  const headerList = headers();
  const tenantHost = headerList.get("x-forwarded-host") ?? headerList.get("host") ?? "";
  const tenantContext = getTenantContext(tenantHost);
  let theme = defaultTenantTheme;

  if (tenantContext.slug) {
    try {
      const tenantTheme = await getPublicTenantTheme(tenantHost);
      theme = {
        logoLightUrl: tenantTheme.logoLightUrl,
        logoDarkUrl: tenantTheme.logoDarkUrl,
        primaryColor: tenantTheme.primaryColor,
        secondaryColor: tenantTheme.secondaryColor,
        heroImageUrl: tenantTheme.heroImageUrl,
        faviconUrl: tenantTheme.faviconUrl ?? undefined,
      };
    } catch {
      theme = defaultTenantTheme;
    }
  }

  return (
    <html lang="cs" suppressHydrationWarning style={tenantThemeToCssVars(theme) as CSSProperties}>
      <head>
        {theme.faviconUrl ? <link rel="icon" href={theme.faviconUrl} /> : null}
        <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
        <link rel="manifest" href="/site.webmanifest" />
        <meta name="theme-color" content="#1a1f3c" />
        <meta name="apple-mobile-web-app-capable" content="yes" />
        <meta name="apple-mobile-web-app-status-bar-style" content="default" />
        <meta name="apple-mobile-web-app-title" content="KlikniLístek" />
        <link rel="apple-touch-icon" href="/apple-touch-icon.png" />
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="" />
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=Playfair+Display:wght@400;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="bg-background text-foreground antialiased font-body">
        {children}
      </body>
    </html>
  );
}
