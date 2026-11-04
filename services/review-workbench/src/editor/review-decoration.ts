import { Extension, type Editor } from "@tiptap/core";
import { Plugin, PluginKey } from "@tiptap/pm/state";
import { Decoration, DecorationSet } from "@tiptap/pm/view";
import {
  renderedRange,
  resolveLocation,
  type ReviewLocation,
} from "./review-highlights.ts";
const key = new PluginKey<DecorationSet>("review-issues");
export const ReviewDecoration = Extension.create({
  name: "reviewDecoration",
  addProseMirrorPlugins() {
    return [
      new Plugin({
        key,
        state: {
          init: () => DecorationSet.empty,
          apply: (tr, old) => tr.getMeta(key) ?? old.map(tr.mapping, tr.doc),
        },
        props: { decorations: (s) => key.getState(s) },
      }),
    ];
  },
});
export function decorateEditor(
  editor: Editor,
  raw: string,
  issues: ReviewLocation[],
  active?: string,
) {
  const positions: number[] = [];
  let joined = "";
  editor.state.doc.descendants((node, pos) => {
    if (node.isText && node.text) {
      joined += node.text;
      for (let i = 0; i < node.text.length; i++) positions.push(pos + i);
    }
  });
  const marks: Decoration[] = [];
  for (const issue of issues) {
    if (!resolveLocation(raw, issue)) continue;
    const range = renderedRange(joined, issue);
    if (!range) continue;
    if(range.start===range.end){
      const position=positions[range.start]??(positions.at(-1)??0)+1;
      marks.push(Decoration.widget(position,()=>{const mark=document.createElement('span');mark.dataset.reviewId=issue.id;mark.className='review-mark review-mark-active';mark.textContent='▏';return mark;}));continue;
    }
    const from = positions[range.start],
      to = positions[range.end - 1] + 1;
    if (from === undefined || !Number.isFinite(to)) continue;
    marks.push(
      Decoration.inline(from, to, {
        "data-review-id": issue.id,
        class:
          issue.id === active
            ? "review-mark review-mark-active"
            : "review-mark",
      }),
    );
  }
  editor.view.dispatch(
    editor.state.tr
      .setMeta(key, DecorationSet.create(editor.state.doc, marks))
      .setMeta("addToHistory", false),
  );
}
