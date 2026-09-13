import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import PlatformApp from "./PlatformApp.tsx";
import { HashRouter } from "react-router-dom";
import "./theme.css";
import "./platform.css";

const query = new QueryClient({
  defaultOptions: {
    queries: { staleTime: 30000, retry: 1 },
    mutations: { retry: false },
  },
});
createRoot(document.getElementById("root")!).render(
  <QueryClientProvider client={query}>
    <HashRouter>
      <PlatformApp />
    </HashRouter>
  </QueryClientProvider>,
);
