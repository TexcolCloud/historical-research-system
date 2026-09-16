import { lazy, Suspense, type ComponentProps } from "react";
const Editor = lazy(() => import("@/editor/MarkdownEditor.tsx").then(module => ({ default: module.MarkdownEditor })));
export function MarkdownEditor(props: ComponentProps<typeof Editor>) {
  return <Suspense fallback={<p role="status">正在加载编辑器…</p>}><Editor {...props} /></Suspense>;
}
