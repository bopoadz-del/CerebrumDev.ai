import React from "react";

/** Automotive branding constants applied by the generator text filter + overlay. */
export const AUTOMOTIVE_BRAND = {
  productName: "Automotive Safety Intelligence",
  workspaceName: "Vehicle Safety Workspace",
  foundationName: "Automotive Core Knowledge",
  nav: ["Vehicle Safety Workspace", "Automotive Core Knowledge", "My Projects", "Admin"],
  quickActions: [
    "Check recalls by vehicle",
    "Search campaign number",
    "Review complaint patterns",
    "Compare vehicle safety records",
    "Summarize an investigation",
  ],
};

export function CitationLabel({
  knowledgeLayer,
}: {
  knowledgeLayer: string;
}) {
  const label =
    knowledgeLayer === "client_private" ? "Private Project" : "Automotive Core";
  return <span className="citation-label">[{label}]</span>;
}

export default function AutomotiveBrandingBanner() {
  return (
    <header className="automotive-branding">
      <h1>{AUTOMOTIVE_BRAND.productName}</h1>
      <p>{AUTOMOTIVE_BRAND.workspaceName}</p>
    </header>
  );
}
