import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AssetDetail from "./AssetDetail";

const getAsset = vi.fn();
const getEvidenceGraph = vi.fn();

vi.mock("../api/client", () => ({
  getAsset: (...args: unknown[]) => getAsset(...args),
  updateAsset: vi.fn(),
  canWrite: () => true,
  getEvidenceGraph: (...args: unknown[]) => getEvidenceGraph(...args),
}));

describe("AssetDetail", () => {
  beforeEach(() => {
    getAsset.mockResolvedValue({
      id: 4758,
      scan_job_id: 35,
      logical_asset_id: "ecdsa-signature-testmock",
      algorithm: "ECDSA",
      category: "asymmetric",
      source: ["rule"],
      location: "vendor/truststore/_macos.py",
      evidence_json: {
        component: "testmock",
        evidence_list: [
          {
            evidence_kind: "observed_operation",
            source: "rule",
            confidence: 0.82,
            evidence: { rule_id: "crypto.ecdsa", pattern: "ECDSA" },
            location: "vendor/truststore/_macos.py",
            span: { line_start: 6 },
          },
        ],
      },
      confidence: 0.9,
      conflict: false,
      quantum_vulnerable: true,
      priority_score: 60,
      priority_label: "HIGH",
      pqc_candidate: "ML-DSA",
      business_criticality: "medium",
      usage: "signature",
      library: "",
      protocol: "",
      key_size: null,
      data_sensitivity: "medium",
      data_lifetime_years: 10,
      migration_time_years: 3,
      threat_horizon_years: 10,
      exposure: "internal",
      migration_effort: "medium",
      risk_reasons: ["Quantum-vulnerable signature"],
      hybrid_recommended: true,
      capability_only: false,
      risk_context_provenance: {},
      created_at: "2026-09-21T14:43:00Z",
    });
    getEvidenceGraph.mockResolvedValue({
      scan_id: 35,
      nodes: [
        {
          id: "asset-4758",
          type: "asset",
          label: "ECDSA",
          logical_asset_id: "ecdsa-signature-testmock",
          priority_score: 60,
          priority_label: "HIGH",
          quantum_vulnerable: true,
        },
        {
          id: "evid-1",
          type: "evidence",
          label: "rule",
          confidence: 0.82,
        },
      ],
      edges: [{ source: "asset-4758", target: "evid-1", relation: "detected_by" }],
    });
  });

  it("separates evidence, editable context, and the computed migration decision", async () => {
    render(
      <MemoryRouter initialEntries={["/assets/4758"]}>
        <Routes>
          <Route path="/assets/:id" element={<AssetDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Discovery assurance" })).toBeVisible();
    expect(screen.getByRole("group", { name: "Business and migration risk inputs" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Mosca planning window" })).toBeVisible();
    expect(screen.getByText("X — Data lifetime")).toBeVisible();
    expect(screen.getByText("Y — Migration time")).toBeVisible();
    expect(screen.getByText("Z — Threat horizon")).toBeVisible();
    expect(screen.getByText("13 years")).toBeVisible();
    expect(
      screen.getByText("The planning window exceeds this modeled horizon by 3 years."),
    ).toBeVisible();
    expect(screen.getByText(/planning scenario, not a prediction/)).toBeVisible();
    expect(screen.getByText("crypto.ecdsa")).toBeVisible();
    expect(screen.getByText("ECDSA", { selector: ".timeline-detail p" })).toBeVisible();

    await waitFor(() => expect(getAsset).toHaveBeenCalledWith(4758));
  });

  it("loads direct evidence relationships for the inspected asset", async () => {
    render(
      <MemoryRouter initialEntries={["/assets/4758"]}>
        <Routes>
          <Route path="/assets/:id" element={<AssetDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Evidence relationships" })).toBeVisible();
    expect(screen.getByText("Direct evidence supporting this finding in scan #35.")).toBeVisible();
    expect(await screen.findByText("detected by")).toBeVisible();
    await waitFor(() => expect(getEvidenceGraph).toHaveBeenCalledWith(35, 4758));
  });

  it("shows a muted message when graph data is empty", async () => {
    getEvidenceGraph.mockResolvedValueOnce({
      scan_id: 35,
      nodes: [],
      edges: [],
    });
    render(
      <MemoryRouter initialEntries={["/assets/4758"]}>
        <Routes>
          <Route path="/assets/:id" element={<AssetDetail />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("No graph data available for this scan.")).toBeVisible();
  });
});
