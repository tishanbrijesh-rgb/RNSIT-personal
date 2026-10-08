import { render, screen } from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes } from "react-router-dom";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { getScanDetail } from "../api/client";
import ScanDetailPage from "./ScanDetailPage";
import type { ScanDetail } from "../types";

vi.mock("../api/client", () => ({
  downloadCsv: vi.fn(),
  getScanDetail: vi.fn(),
  getScans: vi.fn(() => Promise.resolve([])),
  scanRepo: vi.fn(() => Promise.resolve({ scan_id: 99, status: "queued" })),
  canWrite: vi.fn(() => true),
}));

describe("ScanDetailPage", () => {
  it("clears a previous scan error when navigating to another scan", async () => {
    vi.mocked(getScanDetail)
      .mockRejectedValueOnce(new Error("Missing scan"))
      .mockImplementationOnce(() => new Promise(() => undefined));
    render(
      <MemoryRouter initialEntries={["/scans/7"]}>
        <Link to="/scans/8">Next scan</Link>
        <Routes>
          <Route path="/scans/:id" element={<ScanDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );
    expect(await screen.findByRole("alert")).toHaveTextContent("Missing scan");
    await userEvent.click(screen.getByRole("link", { name: "Next scan" }));
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("transitions from loading to the scan details", async () => {
    vi.mocked(getScanDetail).mockResolvedValue({
      id: 7,
      repo_path: "/repo",
      status: "completed",
      started_at: null,
      finished_at: null,
      assets_found: 0,
      avg_confidence: null,
      total_files: 1,
      in_scope_files: 1,
      scanned_files: 1,
      failed_files: 0,
      coverage_pct: 100,
      duration_ms: 10,
      collector_stats: {},
      blind_spots: [],
      assets: [],
      assets_total: 0,
      summary: {
        total_assets: 0,
        high_risk_count: 0,
        avg_confidence: 0,
        coverage_pct: 100,
        blind_spots: [],
        risk_distribution: { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 },
        quantum_vulnerable_count: 0,
        conflict_count: 0,
        latest_scan_id: 7,
        collector_stats: {},
      },
    });

    render(
      <MemoryRouter initialEntries={["/scans/7"]}>
        <Routes>
          <Route path="/scans/:id" element={<ScanDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(await screen.findByRole("heading", { name: "Scan #7" })).toBeInTheDocument();
  });

  it("shows sanitized failed file details", async () => {
    vi.mocked(getScanDetail).mockResolvedValue({
      id: 8,
      repo_path: "/repo",
      status: "completed",
      started_at: null,
      finished_at: null,
      assets_found: 0,
      avg_confidence: null,
      total_files: 2,
      in_scope_files: 2,
      scanned_files: 1,
      failed_files: 1,
      coverage_pct: 50,
      duration_ms: 10,
      collector_stats: {},
      blind_spots: [],
      failures: [{ path: "certs/broken.pem", reason: "certificate_error" }],
      assets: [],
      assets_total: 0,
      summary: {
        total_assets: 0,
        high_risk_count: 0,
        avg_confidence: 0,
        coverage_pct: 50,
        blind_spots: [],
        risk_distribution: { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 },
        quantum_vulnerable_count: 0,
        conflict_count: 0,
        latest_scan_id: 8,
        collector_stats: {},
      },
    } as ScanDetail);

    render(
      <MemoryRouter initialEntries={["/scans/8"]}>
        <Routes>
          <Route path="/scans/:id" element={<ScanDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("heading", { name: "Failed files" })).toBeInTheDocument();
    expect(screen.getByText("certs/broken.pem")).toBeInTheDocument();
    expect(screen.getByText("Certificate error")).toBeInTheDocument();
  });

  it("shows full-scan risk totals instead of totals from the asset preview", async () => {
    vi.mocked(getScanDetail).mockResolvedValue({
      id: 35,
      repo_path: "C:\\Python314",
      status: "completed",
      started_at: null,
      finished_at: null,
      assets_found: 1081,
      avg_confidence: 0.84,
      total_files: 3971,
      in_scope_files: 2312,
      scanned_files: 2301,
      failed_files: 11,
      coverage_pct: 99.52,
      duration_ms: 239000,
      collector_stats: { ast: 123, rule: 828, dep: 0, cert: 131 },
      blind_spots: [],
      assets: [],
      assets_total: 1081,
      summary: {
        total_assets: 1081,
        high_risk_count: 361,
        avg_confidence: 0.84,
        coverage_pct: 99.52,
        blind_spots: [],
        risk_distribution: { CRITICAL: 0, HIGH: 361, MEDIUM: 0, LOW: 720 },
        quantum_vulnerable_count: 361,
        conflict_count: 0,
        latest_scan_id: 35,
        collector_stats: { ast: 123, rule: 828, dep: 0, cert: 131 },
      },
    });

    render(
      <MemoryRouter initialEntries={["/scans/35"]}>
        <Routes>
          <Route path="/scans/:id" element={<ScanDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("All 1,081 findings, not the preview")).toBeInTheDocument();
    expect(screen.getAllByText("361")).toHaveLength(2);
    expect(screen.getByText("720")).toBeInTheDocument();
    expect(screen.getByText(/Coverage does not measure detection accuracy/)).toBeInTheDocument();
  });

  it("offers a rescan button that reruns the scan", async () => {
    const { scanRepo } = await import("../api/client");
    vi.mocked(getScanDetail).mockResolvedValue({
      id: 7,
      repo_path: "/repo",
      status: "completed",
      started_at: null,
      finished_at: null,
      assets_found: 0,
      avg_confidence: null,
      total_files: 1,
      in_scope_files: 1,
      scanned_files: 1,
      failed_files: 0,
      coverage_pct: 100,
      duration_ms: 10,
      collector_stats: {},
      blind_spots: [],
      assets: [],
      assets_total: 0,
      summary: {
        total_assets: 0,
        high_risk_count: 0,
        avg_confidence: 0,
        coverage_pct: 100,
        blind_spots: [],
        risk_distribution: { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 0 },
        quantum_vulnerable_count: 0,
        conflict_count: 0,
        latest_scan_id: 7,
        collector_stats: {},
      },
    });

    render(
      <MemoryRouter initialEntries={["/scans/7"]}>
        <Routes>
          <Route path="/scans/:id" element={<ScanDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const btn = await screen.findByRole("button", { name: /rescan/i });
    expect(btn).toBeEnabled();
    await userEvent.click(btn);
    expect(scanRepo).toHaveBeenCalledWith("/repo");
  });
});
