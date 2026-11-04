import { Component, type ReactNode } from "react";
import { Button } from "@/components/ui/button.tsx";

export default class WorkspaceBoundary extends Component<
  { children: ReactNode },
  { failed: boolean }
> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    if (this.state.failed)
      return (
        <section className="platform-empty" role="alert">
          <h1>页面加载遇到问题</h1>
          <p>后台任务和已保存的草稿仍保留。刷新页面可重新加载工作台。</p>
          <Button onClick={() => window.location.reload()}>刷新页面</Button>
        </section>
      );
    return this.props.children;
  }
}
