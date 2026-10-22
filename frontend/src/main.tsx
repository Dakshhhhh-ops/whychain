import "@fontsource-variable/inter";
import "@fontsource-variable/jetbrains-mono";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { DatasetProvider } from "./lib/dataset";
import "./index.css";
import { History } from "./pages/History";
import { Home } from "./pages/Home";
import { Investigation } from "./pages/Investigation";
import { MemoryPage } from "./pages/MemoryPage";
import { Sources } from "./pages/Sources";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <BrowserRouter>
      <DatasetProvider>
      <Routes>
        <Route element={<Layout />}>
          <Route index element={<Home />} />
          <Route path="runs" element={<History />} />
          <Route path="runs/:id" element={<Investigation />} />
          <Route path="memory" element={<MemoryPage />} />
          <Route path="sources" element={<Sources />} />
        </Route>
      </Routes>
      </DatasetProvider>
    </BrowserRouter>
  </StrictMode>,
);
