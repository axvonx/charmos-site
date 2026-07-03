import { defineCollection } from 'astro:content';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';
import { blogSchema } from 'starlight-blog/schema';
import { pageThemeObsidianSchema } from 'starlight-theme-obsidian/schema';

export const collections = {
	docs: defineCollection({
		loader: docsLoader(),
		// Merge both plugin schemas into the docs collection: starlight-blog (post
		// frontmatter: date/authors/tags/…) and the obsidian theme (per-page graph
		// + backlinks overrides). Both are ZodObjects, so .merge() composes them.
		schema: docsSchema({
			extend: (context) => blogSchema(context).merge(pageThemeObsidianSchema),
		}),
	}),
};
