// @ts-check
import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";
import starlightThemeObsidian from "starlight-theme-obsidian";

// https://astro.build/config
export default defineConfig({
  base: "/",
  site: "https://docs.charmos.dev",
  integrations: [
    starlight({
      customCss: [
        "./src/styles/global.css",
      ],
      plugins: [
        starlightThemeObsidian({
          sitemapConfig: {},
          graphConfig: {},
          backlinksConfig: {},
        }),
      ],
      tableOfContents: {
        minHeadingLevel: 1,
        maxHeadingLevel: 5,
      },
      title: "charmOS docs",
      social: [
        {
          icon: "github",
          label: "GitHub",
          href: "https://github.com/axvonx/charmos",
        },
      ],
      sidebar: [
        {
          label: "Guides",
          items: [
            // Each item here is one entry in the navigation menu.
            { label: "Example Guide", slug: "guides/example" },
          ],
          collapsed: true,
        },
        {
          label: "Reference",
          autogenerate: { directory: "reference" },
          collapsed: true,
        },
      ],
    }),
  ],
});
