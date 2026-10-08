import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import { getScan, getScans, subscribeScanEvents, type ScanProgressEvent } from "../api/client";

import ScanPage, { progressDisplay, isActiveScanStatus, shouldPollScanStatus } from "./ScanPage";

vi.mock("../api/client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../api/client")>()),
  getScan: vi.fn(),
  getScans: vi.fn(),
  subscribeScanEvents: vi.fn(),
}));

afterEach(() => sessionStorage.removeItem("ecdat.activeScanId"));

describe("scan lifecycle", () => {
  it("keeps the running screen visible for every accepted scan status", () => {
    expect(
      ["started", "queued", "pending", "initialising", "running"].every(isActiveScanStatus),
    ).toBe(true);
  });
});

describe("scan progress fallback", () => {
  it("polls while a scan is active and stops after completion", () => {
    expect(shouldPollScanStatus("running")).toBe(true);
    expect(shouldPollScanStatus("completed")).toBe(false);
  });

  it("shows discovered files while a large repository is being indexed", () => {
    expect(
      progressDisplay({
        scan_id: 7,
        status: "running",
        collector_stats: {
          _phase: "indexing",
          _files_discovered: 8421,
          _files_supported: 312,
        },
        assets_found: 0,
        coverage_pct: 0,
        duration_ms: 500,
      }),
    ).toEqual({ phase: "indexing", files: 8421, label: "Files discovered" });
  });

  it("derives live scan progress from processed and supported files", () => {
    expect(
      progressDisplay({
        scan_id: 38,
        status: "running",
        collector_stats: {
          _phase: "collecting",
          _files_processed: 509,
          _files_supported: 2312,
        },
        assets_found: 0,
        coverage_pct: 0,
        duration_ms: 207000,
      }),
    ).toEqual({
      phase: "collecting",
      files: 509,
      label: "Files processed",
      total: 2312,
      progressPercent: 22.02,
      processingComplete: false,
    });
  });

  it("does not claim completion when all eligible files have been processed", () => {
    const display = progressDisplay({
      scan_id: 38,
      status: "running",
      collector_stats: {
        _phase: "correlating",
        _files_processed: 9,
        _files_supported: 9,
      },
      assets_found: 0,
      coverage_pct: 0,
      duration_ms: 0,
    });
    expect(display.progressPercent).toBeUndefined();
    expect(display.processingComplete).toBe(true);
  });
});

it("keeps the backend timer when progress events omit timestamps", async () => {
  const startedAt = new Date(Date.now() - 65_000).toISOString();
  const liveEvent: ScanProgressEvent = {
    scan_id: 51,
    status: "running",
    collector_stats: { _phase: "collecting", _files_processed: 226, _files_supported: 2312 },
    assets_found: 0,
    coverage_pct: 0,
    duration_ms: 0,
  };
  let emitProgress: ((event: ScanProgressEvent) => void) | undefined;
  const closeEvents = vi.fn();
  vi.mocked(getScans).mockResolvedValue([]);
  vi.mocked(getScan).mockResolvedValue({
    id: 51,
    repo_path: "C:\\Python314",
    status: "running",
    started_at: startedAt,
    finished_at: null,
    assets_found: 0,
    avg_confidence: null,
    total_files: 2312,
    in_scope_files: 2312,
    scanned_files: 0,
    failed_files: 0,
    coverage_pct: 0,
    duration_ms: 0,
    collector_stats: {},
    blind_spots: [],
  });
  vi.mocked(subscribeScanEvents).mockImplementation((_id, onEvent) => {
    emitProgress = onEvent;
    onEvent(liveEvent);
    return closeEvents;
  });
  sessionStorage.setItem("ecdat.activeScanId", "51");

  const view = render(
    <MemoryRouter>
      <ScanPage />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/^1m \d+s$/)).toBeInTheDocument());
  expect(screen.getByText("226 / 2,312")).toBeInTheDocument();
  expect(screen.getByText("9.78%")).toBeInTheDocument();
  expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "9.78");
  expect(subscribeScanEvents).toHaveBeenCalledTimes(1);

  act(() =>
    emitProgress?.({
      ...liveEvent,
      collector_stats: { ...liveEvent.collector_stats, _files_processed: 300 },
    }),
  );
  expect(screen.getByText("300 / 2,312")).toBeInTheDocument();
  expect(screen.getByText(/^1m \d+s$/)).toBeInTheDocument();

  view.unmount();
  expect(closeEvents).toHaveBeenCalledTimes(1);
  render(
    <MemoryRouter>
      <ScanPage />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.getByText(/^1m \d+s$/)).toBeInTheDocument());
  expect(subscribeScanEvents).toHaveBeenCalledTimes(2);
});
