import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter } from "react-router-dom";
import type { DashboardSummary, ScanJob } from "../types";
import { describe, expect, it, vi } from "vitest";
import {
  canWrite,
  downloadReport,
  getDashboardSummary,
  getEvaluation,
  getScans,
} from "../api/client";
import Dashboard from "./Dashboard";

vi.mock("../api/client", () => ({
  canWrite: vi.fn(() => false),
  downloadReport: vi.fn(),
  getDashboardSummary: vi.fn(),
  getEvaluation: vi.fn(),
  getScans: vi.fn(() => Promise.resolve([])),
  getAssets: vi.fn(() => Promise.resolve({ items: [], total: 0, page: 0, page_size: 0 })),
}));

vi.mock("../components/RiskDistributionChart", () => ({
  default: () => <div>Risk chart</div>,
}));

describe("Dashboard", () => {
  const summary: DashboardSummary = {
    total_assets: 1,
    high_risk_count: 0,
    avg_confidence: 0.9,
    coverage_pct: 100,
    blind_spots: [],
    risk_distribution: { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 1 },
    quantum_vulnerable_count: 0,
    conflict_count: 0,
    latest_scan_id: 7,
    collector_stats: {},
  };

  it("keeps the dashboard available after a failed download", async () => {
    vi.mocked(getDashboardSummary).mockResolvedValue(summary);
    vi.mocked(getEvaluation).mockRejectedValue(new Error("unavailable"));
    vi.mocked(downloadReport).mockRejectedValue(new Error("Download failed"));
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Download risk report" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Download failed");
    expect(
      screen.getByRole("heading", { name: "Cryptographic assurance overview" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Download risk report" })).toBeEnabled();
  });

  it("ignores an older scan failure after navigation", async () => {
    let rejectOld!: (error: Error) => void;
    vi.mocked(getDashboardSummary)
      .mockImplementationOnce(
        () =>
          new Promise((_, reject) => {
            rejectOld = reject;
          }),
      )
      .mockResolvedValueOnce(summary);
    vi.mocked(getEvaluation).mockRejectedValue(new Error("unavailable"));
    render(
      <MemoryRouter initialEntries={["/?scan_id=8"]}>
        <Link to="/?scan_id=7">Next scan</Link>
        <Dashboard />
      </MemoryRouter>,
    );
    await userEvent.click(screen.getByRole("link", { name: "Next scan" }));
    expect(
      await screen.findByRole("heading", { name: "Cryptographic assurance overview" }),
    ).toBeInTheDocument();
    expect(screen.getByText("Avg evidence confidence")).toBeInTheDocument();
    expect(screen.getByText("Evaluation-corpus precision / recall")).toBeInTheDocument();
    expect(screen.getByRole("navigation", { name: "Judge demo path" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /1 Start scan/ })).toHaveAttribute("href", "/scan");
    expect(screen.getByRole("link", { name: /2 Verify history/ })).toHaveAttribute(
      "href",
      "/scans",
    );
    expect(screen.getByRole("link", { name: /4 Export CBOM/ })).toHaveAttribute(
      "href",
      "/cbom?scan_id=7",
    );
    expect(screen.getByText("No Critical or High findings")).toBeInTheDocument();
    expect(screen.getByText("Not measured")).toBeInTheDocument();
    await act(async () => rejectOld(new Error("Old scan failed")));
    expect(screen.queryByText("Dashboard unavailable")).not.toBeInTheDocument();
  });

  it("transitions from loading to the populated summary", async () => {
    vi.mocked(getDashboardSummary).mockResolvedValue({
      total_assets: 1,
      high_risk_count: 0,
      avg_confidence: 0.9,
      coverage_pct: 100,
      blind_spots: [],
      risk_distribution: { CRITICAL: 0, HIGH: 0, MEDIUM: 0, LOW: 1 },
      quantum_vulnerable_count: 0,
      conflict_count: 0,
      latest_scan_id: 7,
      collector_stats: { ast: 1 },
    });
    vi.mocked(getEvaluation).mockRejectedValue(new Error("not available"));

    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    );

    // Loading state shows skeleton placeholders with a status role
    expect(screen.getByRole("status")).toBeInTheDocument();
    expect(
      await screen.findByRole("heading", { name: "Cryptographic assurance overview" }),
    ).toBeInTheDocument();
  });

  it("keeps the selected scan in downloads and CBOM navigation", async () => {
    vi.mocked(getDashboardSummary).mockResolvedValue(summary);
    vi.mocked(getEvaluation).mockRejectedValue(new Error("unavailable"));
    render(
      <MemoryRouter initialEntries={["/?scan_id=7"]}>
        <Dashboard />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("button", { name: "Download risk report" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "View CBOM" })).toHaveAttribute(
      "href",
      "/cbom?scan_id=7",
    );
  });

  it("shows recent scan rescan button when write access is available", async () => {
    vi.mocked(getDashboardSummary).mockResolvedValue(summary);
    vi.mocked(getEvaluation).mockRejectedValue(new Error("unavailable"));
    vi.mocked(canWrite).mockReturnValue(true);
    vi.mocked(getScans).mockResolvedValue([
      {
        id: 1,
        repo_path: "/repo",
        status: "completed",
        started_at: new Date().toISOString(),
        finished_at: new Date().toISOString(),
        assets_found: 0,
        avg_confidence: null,
        total_files: 0,
        in_scope_files: 0,
        scanned_files: 0,
        failed_files: 0,
        coverage_pct: 100,
        duration_ms: 1000,
        collector_stats: {},
        blind_spots: [],
        failures: [],
      } satisfies ScanJob,
    ]);
    render(
      <MemoryRouter>
        <Dashboard />
      </MemoryRouter>,
    );
    expect(await screen.findByRole("button", { name: "Rescan" })).toBeInTheDocument();
  });
});
