// @ts-check
import { fileURLToPath } from "node:url";
import starlight from "@astrojs/starlight";
import { defineConfig } from "astro/config";
import starlightThemeObsidian from "starlight-theme-obsidian";
import starlightBlog from "starlight-blog";
import { visit } from "unist-util-visit";
import { toString } from "hast-util-to-string";

// Reference API construct headings are authored as "### {kind} {name}" (e.g.
// "struct thread"). This plugin, running before Astro assigns heading ids +
// collects the TOC, rewrites them to: display just "{name}", a deterministic
// id "{kind}-{name}" (so cross-page links resolve without slug guessing), and a
// class the CSS hides in the body while keeping the TOC entry.
const API_KINDS = ["struct", "union", "enum", "type alias", "function", "macro", "variable"];
function rehypeApiHeadings() {
  const re = new RegExp(`^(${API_KINDS.join("|")}) (.+)$`);
  return (tree) => {
    visit(tree, "element", (node) => {
      if (!/^h[1-6]$/.test(node.tagName)) return;
      const m = toString(node).match(re);
      if (!m) return;
      const kind = m[1].replace(/ /g, "-");
      const name = m[2];
      node.properties = node.properties || {};
      node.properties.id = `${kind}-${name.toLowerCase()}`;
      node.properties.className = [
        ...(node.properties.className || []),
        "api-symbol",
      ];
      node.children = [{ type: "text", value: name }];
    });
  };
}

// https://astro.build/config
export default defineConfig({
  base: "/",
  site: "https://docs.charmos.dev",
  markdown: {
    // Runs ahead of Astro's heading-id/TOC collection.
    rehypePlugins: [rehypeApiHeadings],
  },
  // Alias so generated MDX (at any depth) can import shared components by a
  // stable path: `import SourceBlock from '@components/SourceBlock.astro'`.
  vite: {
    resolve: {
      alias: {
        "@components": fileURLToPath(new URL("./src/components", import.meta.url)),
      },
    },
    // starlight-theme-obsidian's graph client bundle pulls in picomatch, a
    // Node-only glob lib that reads process.platform / process.version. Those
    // are undefined in the browser, so the graph script threw "process is not
    // defined" on every page (killing graph hydration + spamming the console).
    // Replace the tokens at build time so no runtime `process` lookup happens.
    define: {
      "process.platform": JSON.stringify("browser"),
      "process.version": JSON.stringify(process.version),
    },
  },
  integrations: [
    starlight({
      customCss: [
        "./src/styles/global.css",
      ],
      plugins: [
        // Blog lives at /blog, authored under content/blog/ (assembled into
        // src/content/docs/blog/). Registered before the theme so the theme
        // wraps the blog routes too.
        starlightBlog({
          title: "Blog",
          postCount: 10,
        }),
        starlightThemeObsidian({
          sitemapConfig: {},
          graphConfig: {
            // Directed edges: an arrow points from the page that references to
            // the page it references. So from the current node, an outgoing
            // arrow = "we reference this", an incoming arrow = "this references
            // us". The `render-arrows` toolbar button toggles them at runtime.
            renderArrows: true,
          },
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
        // Declare the RSS social link ourselves (relative) so it resolves on
        // both the local preview and the deployed site — starlight-blog would
        // otherwise inject an absolute `site`-based URL that 404s locally.
        {
          icon: "rss",
          label: "RSS",
          href: "/blog/rss.xml",
        },
      ],
      sidebar: [
        {
          label: "Design",
          link: "/design/",
        },
        {
          label: "Guides",
          autogenerate: { directory: "guides" },
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
