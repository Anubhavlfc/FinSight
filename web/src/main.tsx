import "@fontsource/ibm-plex-sans/latin-400.css";
import "@fontsource/ibm-plex-sans/latin-500.css";
import "@fontsource/ibm-plex-sans/latin-600.css";
import "@fontsource/ibm-plex-mono/latin-400.css";
import "@fontsource/ibm-plex-mono/latin-500.css";
import "@fontsource-variable/source-serif-4";
import "./styles/base.css";
import "./styles/app.css";
import "./styles/landing.css";

import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { App } from "./App";
import { BASE_URL } from "./config";
import { ServiceProvider } from "./lib/service";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter basename={BASE_URL.replace(/\/$/, "") || "/"}>
      <ServiceProvider>
        <App />
      </ServiceProvider>
    </BrowserRouter>
  </StrictMode>,
);
