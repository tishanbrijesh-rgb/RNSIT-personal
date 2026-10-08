import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { exportAssetsCsv, getAssets, getDashboardSummary } from "../api/client";
import type { CryptoAsset } from "../types";
import AssetsPage from "./AssetsPage";

vi.mock("../api/client", () => ({
  canWrite: vi.fn(() => false),
  getAssets: vi.fn(),
  getDashboardSummary: vi.fn(),
  exportAssetsCsv: vi.fn(),
}));

const previewAsset = {
  id: 1,
  scan_job_id: 35,
  algorithm: "ECDSA",
  category: "signature",
  source: ["rule"],
  location: "Lib/vendor/example.py",
  evidence_json: {},
  confidence: 0.9,
  conflict: false,
  quantum_vulnerable: true,
  priority_score: 60,
  priority_label: "HIGH",
  usage: "signature",
  library: "",
  protocol: "",
  key_size: null,
} as CryptoAsset;

describe("AssetsPage", () => {
  it("uses full-scan aggregates instead of deriving charts from the current page", async () => {
    vi.mocked(getAssets).mockResolvedValue({ items: [previewAsset], total: 1081 });
    vi.mocked(getDashboardSummary).mockResolvedValue({
      total_assets: 1081,
      high_risk_count: 361,
      avg_confidence: 0.84,
      coverage_pct: 99.52,
      blind_spots: [],
      risk_distribution: { CRITICAL: 0, HIGH: 361, MEDIUM: 0, LOW: 720 },
      quantum_vulnerable_count: 361,
      conflict_count: 0,
      latest_scan_id: 35,
      collector_stats: {},
      confidence_distribution: { "0-20": 0, "21-40": 0, "41-60": 0, "61-80": 50, "81-100": 1031 },
    });

    render(
      <MemoryRouter initialEntries={["/assets?scan_id=35"]}>
        <AssetsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("1,081")).toBeInTheDocument();
    expect(screen.getByText("361")).toBeInTheDocument();
    expect(screen.getByText("720")).toBeInTheDocument();
    expect(screen.getByTitle("81–100%: 1031 assets")).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: /Inspect/ })).toHaveLength(2);
  });

  it("exports all matching assets and only checked asset IDs", async () => {
    const second = { ...previewAsset, id: 2, algorithm: "RSA", location: "src/rsa.py" };
    vi.mocked(getAssets).mockResolvedValue({ items: [previewAsset, second], total: 2 });
    vi.mocked(getDashboardSummary).mockResolvedValue({
      total_assets: 2,
      high_risk_count: 2,
      avg_confidence: 0.9,
      coverage_pct: 100,
      blind_spots: [],
      risk_distribution: { CRITICAL: 0, HIGH: 2, MEDIUM: 0, LOW: 0 },
      quantum_vulnerable_count: 2,
      conflict_count: 0,
      latest_scan_id: 35,
      collector_stats: {},
      confidence_distribution: { "0-20": 0, "21-40": 0, "41-60": 0, "61-80": 0, "81-100": 2 },
    });
    vi.mocked(exportAssetsCsv).mockResolvedValue();
    render(
      <MemoryRouter initialEntries={["/assets?scan_id=35"]}>
        <AssetsPage />
      </MemoryRouter>,
    );
    await screen.findByText("2 findings in scope");
    expect(screen.queryByText("Export selected")).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Export CSV" }));
    await waitFor(() =>
      expect(exportAssetsCsv).toHaveBeenCalledWith(35, expect.objectContaining({ ids: undefined })),
    );
    const boxes = screen.getAllByRole("checkbox", { name: /Select (ECDSA|RSA) at/ });
    fireEvent.click(boxes[0]);
    expect(await screen.findByText("1 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Export selected" }));
    await waitFor(() =>
      expect(exportAssetsCsv).toHaveBeenLastCalledWith(35, expect.objectContaining({ ids: [1] })),
    );
    fireEvent.click(boxes[1]);
    expect(await screen.findByText("2 selected")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Export selected" }));
    await waitFor(() =>
      expect(exportAssetsCsv).toHaveBeenLastCalledWith(
        35,
        expect.objectContaining({ ids: [1, 2] }),
      ),
    );
  });
});
