import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { HashRouter } from "react-router-dom";
import App from "./App";
import { ToastProvider } from "./components/ui";
import "./styles.css";
import { applyCachedTheme } from "./theme";
import checkpointLogo from "../../icon/checkpoint_logo.svg";

applyCachedTheme();

// Use the versioned sidebar asset for the browser icon as well.
const favicon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
if (favicon) favicon.href = checkpointLogo;

const client = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: true, staleTime: 1000 },
    mutations: { retry: 0 },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={client}>
      <ToastProvider>
        <HashRouter>
          <App />
        </HashRouter>
      </ToastProvider>
    </QueryClientProvider>
  </StrictMode>,
);
