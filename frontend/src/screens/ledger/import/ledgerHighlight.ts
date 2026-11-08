import { Decoration, type DecorationSet, EditorView, ViewPlugin, type ViewUpdate } from "@codemirror/view";
import { RangeSetBuilder } from "@codemirror/state";

export type HighlightSpan = { from: number; to: number; className: string };

const HEADER_LINE_RE = /^(\d{4}\/\d{2}\/\d{2})( )(.*?)(?:( ; )(.*))?$/;
const POSTING_LINE_RE = /^(\s+)(\S.*?)(\s{2,})(-?\d+(?:\.\d+)?)(?: ([A-Z]{3}))?$/;
const TAG_LINE_RE = /^\s*#\S+(\s+#\S+)*\s*$/;

export function buildHighlightSpans(text: string): HighlightSpan[] {
  const spans: HighlightSpan[] = [];
  let offset = 0;
  for (const line of text.split("\n")) {
    const headerMatch = HEADER_LINE_RE.exec(line);
    const postingMatch = POSTING_LINE_RE.exec(line);

    if (TAG_LINE_RE.test(line)) {
      spans.push({ from: offset, to: offset + line.length, className: "lg-tag" });
    } else if (headerMatch) {
      const [, date, , narr, , comment] = headerMatch;
      let pos = offset;
      spans.push({ from: pos, to: pos + date.length, className: "lg-date" });
      pos += date.length + 1;
      spans.push({ from: pos, to: pos + narr.length, className: "lg-narr" });
      if (comment !== undefined) {
        // The comment span covers from the leading "; " delimiter's semicolon through
        // end of line, not just the captured comment text after the delimiter.
        const semiIdx = line.indexOf(";", pos - offset);
        spans.push({ from: offset + semiIdx, to: offset + line.length, className: "lg-comment" });
      }
    } else if (postingMatch) {
      const [, lead, account, gap, amount, currency] = postingMatch;
      let pos = offset + lead.length;
      spans.push({ from: pos, to: pos + account.length, className: "lg-account" });
      pos += account.length + gap.length;
      spans.push({ from: pos, to: pos + amount.length, className: "lg-amount" });
      if (currency) {
        const curStart = offset + line.length - currency.length;
        spans.push({ from: curStart, to: offset + line.length, className: "lg-currency" });
      }
    }
    offset += line.length + 1; // account for the split "\n"
  }
  return spans;
}

function decorationsFor(view: EditorView): DecorationSet {
  const builder = new RangeSetBuilder<Decoration>();
  const spans = buildHighlightSpans(view.state.doc.toString());
  for (const span of spans) {
    builder.add(span.from, span.to, Decoration.mark({ class: span.className }));
  }
  return builder.finish();
}

export const ledgerHighlightPlugin = ViewPlugin.fromClass(
  class {
    decorations: DecorationSet;
    constructor(view: EditorView) {
      this.decorations = decorationsFor(view);
    }
    update(update: ViewUpdate) {
      if (update.docChanged) this.decorations = decorationsFor(update.view);
    }
  },
  { decorations: (v) => v.decorations },
);
