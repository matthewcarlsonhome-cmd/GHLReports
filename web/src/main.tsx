// Entry point for the whole app. Vite loads this file first (index.html points
// at it), and everything else — routes, pages, components — hangs off <App />.
//
// Key ideas:
// - ReactDOM.createRoot(...).render(...) mounts the React tree into the single
//   <div id="root"> in index.html. This is a "single-page app": one HTML page,
//   with React swapping views client-side instead of full page loads.
// - <React.StrictMode> is a development-only helper that double-invokes effects
//   and warns about unsafe patterns. It renders nothing and is stripped from
//   production behavior.
// - <BrowserRouter> enables react-router: it watches the URL bar and lets
//   <Route>/<Link> components (see App.tsx) decide what to render, without
//   asking the server for a new page.
import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";

import App from "./App";
import "./index.css";

// The "!" tells TypeScript the #root element definitely exists (it's hard-coded
// in index.html, so this can't be null at runtime).
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <BrowserRouter>
      <App />
    </BrowserRouter>
  </React.StrictMode>,
);
