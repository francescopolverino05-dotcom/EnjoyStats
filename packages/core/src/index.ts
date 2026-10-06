import defaultTags from "./tags/default-tags.json";
import type { TagDefinition } from "./types";

export * from "./types";
export * from "./stats";
export * from "./xml/oncesport";

export function loadDefaultTags(): TagDefinition[] {
  return defaultTags as TagDefinition[];
}

export function onceSportNameForTagId(
  tagId: string,
  tags: TagDefinition[] = loadDefaultTags(),
): string {
  const found = tags.find((t) => t.id === tagId);
  if (!found) {
    throw new Error(`Unknown tag id: ${tagId}`);
  }
  return found.onceSportName;
}
