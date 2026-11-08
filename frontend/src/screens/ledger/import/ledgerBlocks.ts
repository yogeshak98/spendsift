import type { Text } from "@codemirror/state";

export type EntryBlock = {
  /** 1-indexed line number of the header (date/narration) line. */
  headerLine: number;
  /** 1-indexed line numbers of every line belonging to this entry, including the header. */
  lines: number[];
  /** 1-indexed line number of the first posting line (the category leg), if any. */
  categoryLine: number | null;
};

const HEADER_RE = /^\d{4}\/\d{2}\/\d{2}\s/;

export function computeEntryBlocks(doc: Text): EntryBlock[] {
  const blocks: EntryBlock[] = [];
  let current: EntryBlock | null = null;
  for (let i = 1; i <= doc.lines; i++) {
    const text = doc.line(i).text;
    if (text.trim() === "") {
      current = null;
      continue;
    }
    if (HEADER_RE.test(text)) {
      current = { headerLine: i, lines: [i], categoryLine: null };
      blocks.push(current);
    } else if (current) {
      current.lines.push(i);
      if (current.categoryLine === null && !text.trim().startsWith("#")) {
        current.categoryLine = i;
      }
    }
  }
  return blocks;
}
