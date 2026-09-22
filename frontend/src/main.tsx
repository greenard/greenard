import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "maplibre-gl/dist/maplibre-gl.css";
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import App from "./App";
import { AuthProvider } from "./auth/AuthContext";
import "./i18n";
import "./styles.css";
import { PrefsProvider } from "./utils/prefs";

const queryClient = new QueryClient({ defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } } });

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <PrefsProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
        </PrefsProvider>
      </AuthProvider>
    </QueryClientProvider>
  </React.StrictMode>,
);
