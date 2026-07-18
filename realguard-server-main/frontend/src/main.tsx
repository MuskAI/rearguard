import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { initialAnalyticsPage, trackConfirmedPageview } from "./analytics";
import "./styles.css";
import "./refined.css";

trackConfirmedPageview(initialAnalyticsPage());

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
